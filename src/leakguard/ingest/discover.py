"""Walk the raw data folder.

Convention: <raw_dir>/<portfolio_slug>/<property_code>/<source_name>/<files>
Files may sit in subfolders under the source folder. Names starting with
'.', '_' or '~$' (Excel lock files) are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from leakguard.schemas import SOURCE_TARGETS


@dataclass(frozen=True)
class DiscoveredFile:
    path: Path
    relative_path: str
    portfolio_slug: str
    property_code: str
    source_name: str


@dataclass
class DiscoveryResult:
    files: list[DiscoveredFile]
    problems: list[str]


def _ignored(path: Path) -> bool:
    return path.name.startswith((".", "_", "~$"))


def _dirs(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_dir() and not _ignored(p))


def discover(raw_dir: Path, known: dict[str, set[str]]) -> DiscoveryResult:
    """known: portfolio slug -> property codes configured for this mode."""
    files: list[DiscoveredFile] = []
    problems: list[str] = []
    if not raw_dir.exists():
        return DiscoveryResult(files, [f"Folder {raw_dir} does not exist yet."])

    for portfolio_dir in _dirs(raw_dir):
        slug = portfolio_dir.name
        if slug not in known:
            problems.append(
                f"Folder '{slug}' is not a portfolio in config/portfolios.yaml for this mode. Skipped."
            )
            continue
        for property_dir in _dirs(portfolio_dir):
            code = property_dir.name
            if code not in known[slug]:
                problems.append(
                    f"Property folder '{slug}/{code}' is not listed under {slug} in "
                    "config/portfolios.yaml. Skipped."
                )
                continue
            for source_dir in _dirs(property_dir):
                if source_dir.name not in SOURCE_TARGETS:
                    problems.append(
                        f"Folder '{slug}/{code}/{source_dir.name}' is not a known source. "
                        f"Use one of: {', '.join(SOURCE_TARGETS)}."
                    )
                    continue
                for path in sorted(source_dir.rglob("*")):
                    if path.is_file() and not _ignored(path):
                        files.append(
                            DiscoveredFile(
                                path=path,
                                relative_path=path.relative_to(raw_dir).as_posix(),
                                portfolio_slug=slug,
                                property_code=code,
                                source_name=source_dir.name,
                            )
                        )
    return DiscoveryResult(files, problems)
