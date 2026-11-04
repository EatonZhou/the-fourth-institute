#!/usr/bin/env python3
"""Pin an authored explanation's local files to their formal Workspace addresses.

This is an artifact builder, not a registration or permission bypass. The caller
must already be allowed to write the package directory. Home rechecks every
formal address and digest when the package is opened.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_portal.home_explanation_quality import quality as normalize_quality


def build(directory: Path, address: str, *, title: str, summary: str = "", revision: int = 1,
          interaction: str | None = None, supplement: str | None = None,
          video_plan: str | None = None, video: str | None = None,
          captions: str | None = None, transcript: str | None = None, quality: str = 'medium',
          review_assets: list[str] | None = None) -> dict:
    quality = normalize_quality(quality)
    if quality == 'low' and (interaction or video or video_plan or not supplement or not supplement.lower().endswith(('.md', '.markdown'))):
        raise ValueError('low解释页直接使用Markdown supplement')
    root = directory.resolve(strict=True)
    address = address.rstrip("/")
    if not re.fullmatch(r"workspace_[a-f0-9]{12}/file/[^\x00-\x1f]+", address):
        raise ValueError("Use the package's registered Workspace file address")
    if any(part in {".", ".."} for part in address.split("/")) or "\\" in address:
        raise ValueError("Invalid Workspace address")
    if not title.strip() or len(title) > 300 or not isinstance(revision, int) or revision < 1:
        raise ValueError("A title and positive revision are required")
    if not any((interaction, supplement, video_plan, video)):
        raise ValueError("An explanation needs at least one authored module")
    if (captions or transcript) and not video:
        raise ValueError("Captions and transcript require a video")
    if review_assets is not None and (not isinstance(review_assets, list) or len(review_assets) > 32):
        raise ValueError('review_assets must select at most 32 files')

    def asset(relative: str) -> dict:
        name = PurePosixPath(relative)
        if name.is_absolute() or any(p in {"", ".", ".."} for p in relative.split("/")) or "\\" in relative:
            raise ValueError("Assets must be relative files inside the package")
        if any((root.joinpath(*name.parts[:i])).is_symlink() for i in range(1, len(name.parts) + 1)):
            raise ValueError("Assets must not traverse symlinks")
        file = (root / relative).resolve(strict=True)
        if not file.is_relative_to(root) or not file.is_file():
            raise ValueError("Asset escapes the package directory")
        digest = hashlib.sha256()
        with file.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"address": f"{address}/{name.as_posix()}", "sha256": digest.hexdigest()}

    manifest = {"schema": "portal-explanation/v1", "title": title.strip(), "summary": summary,
                "revision": revision, "quality": quality}
    for key, filename in (("interaction", interaction), ("supplement", supplement),
                          ("video_plan", video_plan), ("video", video)):
        if filename:
            manifest[key] = asset(filename)
    if interaction:
        manifest["interaction"]["title"] = "交互图解"
    if captions:
        manifest["video"]["captions"] = asset(captions)
    if transcript:
        manifest["video"]["transcript"] = asset(transcript)
    if review_assets:
        manifest['review_assets'] = list({item['address']: item for item in map(asset, review_assets)}.values())
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--address", required=True, help="workspace_ID/file/package-directory")
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", default="")
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--quality", choices=('low', 'medium', 'high'), required=True,
                        help="Effective requested quality; omission must not silently bypass high review")
    parser.add_argument("--previous-review-id", help="Rejected fixed version this new package corrects")
    parser.add_argument("--authoring-id", help="Formal storyboard task fulfilled by this new fixed plan")
    parser.add_argument("--resident-id", default="", help="Current author; checked against the injected identity")
    parser.add_argument("--reviewer", default="", help="Independent review Resident; otherwise discover the unique configured reviewer")
    parser.add_argument("--task", required=True, help="What the reader should understand or be able to decide")
    for name in ("interaction", "supplement", "video-plan", "video", "captions", "transcript"):
        parser.add_argument("--" + name)
    parser.add_argument('--review-asset', dest='review_assets', action='append', default=[],
                        help='Explicit fixed review dependency inside this package (repeatable, e.g. alignment/source receipt); additions require a new manifest')
    parser.add_argument("--output", default="manifest.json", help="New manifest filename inside the package")
    args = parser.parse_args()
    values = vars(args).copy()
    output = values.pop("output")
    previous_review_id = values.pop('previous_review_id')
    authoring_id = values.pop('authoring_id')
    directory = values.pop("directory")
    resident_id, reviewer, task = (values.pop(key) for key in ("resident_id", "reviewer", "task"))
    manifest = build(directory, **values)
    if Path(output).name != output or output in {".", ".."}:
        parser.error("output must be a filename")
    data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    # Fixed historical manifests must not be silently replaced.
    with (directory / output).open("xb") as stream:
        stream.write(data)
    from scripts.submit_explanation_review import submit, accept_authoring
    target = {"address": args.address.rstrip("/") + "/" + output, "sha256": hashlib.sha256(data).hexdigest()}
    try:
        if authoring_id:
            accept_authoring(target['address'], target['sha256'], args.quality, authoring_id, resident_id)
        review = submit(target["address"], target["sha256"], task, resident_id, reviewer=reviewer, previous_review_id=previous_review_id) if args.quality == 'high' else {'status': 'not_required'}
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, f"Package saved; independent review pending: {exc}\n"
                       "Retry submit_explanation_review.py with this manifest; do not overwrite it.\n")
    print(json.dumps({"view": "explanation", "target": target, "review": review}, ensure_ascii=False))
    if review.get('status') in {'submission_failed', 'blocked', 'cancelled', 'invalidated'}:
        parser.exit(1, 'Package saved; independent review is not complete. Read the fixed review result before continuing.\n')


if __name__ == "__main__":
    main()
