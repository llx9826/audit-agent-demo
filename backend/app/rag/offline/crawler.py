"""Rate-limited OFFLINE crawler; it is never imported by the FastAPI runtime."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from .source_registry import SourceSpec


USER_AGENT = "material-requirement-rag-builder/1.0 (+offline research corpus)"


@dataclass(frozen=True, slots=True)
class CrawlResult:
    source_id: str
    status: str
    path: str | None
    checksum: str | None
    byte_count: int
    error: str | None = None


def _robots_status(url: str, *, timeout_s: float) -> str:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        request = Request(robots_url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=timeout_s) as response:
            parser.parse(response.read().decode("utf-8", errors="replace").splitlines())
    except Exception:
        return "UNAVAILABLE"
    return "ALLOWED" if parser.can_fetch(USER_AGENT, url) else "DISALLOWED"


def _fetch_static(source: SourceSpec, *, timeout_s: float) -> tuple[bytes, str, str]:
    """Use urllib first, then a fixed-argv curl fallback for TLS compatibility."""

    url = str(source.url)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf"})
    try:
        with urlopen(request, timeout=timeout_s) as response:
            return (
                response.read(),
                response.headers.get_content_type() or source.expected_mime_type,
                response.geturl(),
            )
    except Exception as primary_error:
        completed = subprocess.run(
            [
                "curl", "-L", "--fail", "--silent", "--show-error",
                "--max-time", str(max(1, int(timeout_s))),
                "--user-agent", USER_AGENT,
                url,
            ],
            capture_output=True,
            check=False,
            timeout=timeout_s + 5,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"urllib failed ({primary_error}); curl failed ({detail})"
            ) from primary_error
        return completed.stdout, source.expected_mime_type, url


def crawl_sources(
    sources: list[SourceSpec],
    *,
    output_dir: str | Path,
    timeout_s: float = 25,
    delay_s: float = 1,
) -> list[CrawlResult]:
    """Fetch an explicit allowlist once; no link following or anti-bot bypass."""

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    results: list[CrawlResult] = []
    for index, source in enumerate(sources):
        if index:
            time.sleep(delay_s)
        url = str(source.url)
        robots = _robots_status(url, timeout_s=timeout_s)
        if robots == "DISALLOWED":
            results.append(CrawlResult(source.source_id, "SKIPPED_ROBOTS", None, None, 0))
            continue
        try:
            content, content_type, final_url = _fetch_static(source, timeout_s=timeout_s)
            checksum = sha256(content).hexdigest()
            suffix = ".pdf" if content_type == "application/pdf" else ".html"
            source_dir = root / source.source_id
            source_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = source_dir / f"source{suffix}"
            artifact_path.write_bytes(content)
            metadata = {
                **source.model_dump(mode="json"),
                "url": final_url,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "robots_status": robots,
                "content_type": content_type,
                "byte_count": len(content),
                "sha256": checksum,
                "offline_stage": "SOURCE_SNAPSHOT",
            }
            (source_dir / "snapshot.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            results.append(CrawlResult(
                source.source_id,
                "FETCHED",
                str(artifact_path),
                checksum,
                len(content),
            ))
        except Exception as exc:
            results.append(CrawlResult(
                source.source_id,
                "FAILED",
                None,
                None,
                0,
                f"{type(exc).__name__}: {exc}",
            ))
    return results
