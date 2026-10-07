#!/usr/bin/env python
"""Versioned S3 sync for the golden dataset and eval reports (live mode / nightly workflow).

Layout under ``s3://$S3_BUCKET/$S3_PREFIX/``::

    golden/<version>/stockroom_golden_v1.jsonl     immutable per manifest version
    golden/<version>/manifest.json
    golden/<version>/calibration_v1.jsonl
    reports/<timestamp>-<run_id>/eval_results.json | promptfoo_results.json | summary.md

Commands: ``sync-golden`` (download the pinned version, uploading it first if absent),
``upload-golden``, ``download-golden --version``, ``upload-reports``, ``check-models``.
Nothing here creates buckets or roles; the bucket must exist (README > Live mode).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

from stockroom.config import ConfigError, StockroomConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = REPO_ROOT / "data" / "golden"
CALIB_DIR = REPO_ROOT / "data" / "judge_calibration"
REPORTS_DIR = REPO_ROOT / "reports"


def _s3(config: StockroomConfig):
    import boto3

    return boto3.client("s3", region_name=config.aws_region)


def _require_bucket(config: StockroomConfig) -> str:
    if not config.s3_bucket:
        raise ConfigError("S3_BUCKET is not set (see README > Live mode > S3 bucket)")
    return config.s3_bucket


def _manifest() -> dict:
    return json.loads((GOLDEN_DIR / "manifest.json").read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def golden_prefix(config: StockroomConfig, version: str) -> str:
    return f"{config.s3_prefix}/golden/{version}"


def upload_golden(config: StockroomConfig, dry_run: bool = False) -> str:
    bucket = _require_bucket(config)
    version = _manifest()["version"]
    prefix = golden_prefix(config, version)
    files = [
        GOLDEN_DIR / "stockroom_golden_v1.jsonl",
        GOLDEN_DIR / "manifest.json",
        CALIB_DIR / "calibration_v1.jsonl",
    ]
    s3 = None if dry_run else _s3(config)
    for path in files:
        key = f"{prefix}/{path.name}"
        print(f"upload {path.relative_to(REPO_ROOT)} -> s3://{bucket}/{key}")
        if s3 is not None:
            s3.upload_file(
                str(path), bucket, key, ExtraArgs={"Metadata": {"sha256": _sha256(path)}}
            )
    return prefix


def download_golden(
    config: StockroomConfig, version: str, dest: Path, dry_run: bool = False
) -> None:
    bucket = _require_bucket(config)
    prefix = golden_prefix(config, version)
    dest.mkdir(parents=True, exist_ok=True)
    s3 = None if dry_run else _s3(config)
    for name in ("stockroom_golden_v1.jsonl", "manifest.json"):
        key = f"{prefix}/{name}"
        print(f"download s3://{bucket}/{key} -> {dest / name}")
        if s3 is not None:
            s3.download_file(bucket, key, str(dest / name))
    if s3 is not None:
        remote = json.loads((dest / "manifest.json").read_text())
        local_sha = _sha256(dest / "stockroom_golden_v1.jsonl")
        if remote.get("sha256") != local_sha:
            raise RuntimeError(
                f"downloaded golden set sha256 {local_sha} != manifest {remote.get('sha256')}"
            )
        print(f"verified sha256 {local_sha[:12]} for golden {version}")


def sync_golden(config: StockroomConfig, dry_run: bool = False) -> None:
    """Make sure the version pinned in the repo exists in S3, then verify the local copy matches."""
    bucket = _require_bucket(config)
    version = _manifest()["version"]
    key = f"{golden_prefix(config, version)}/manifest.json"
    if dry_run:
        print(f"would ensure s3://{bucket}/{key} exists")
        return
    s3 = _s3(config)
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except s3.exceptions.NoSuchKey:
        print(f"golden {version} not in S3 yet; uploading")
        upload_golden(config)
        return
    remote = json.loads(body)
    local = _manifest()
    if remote.get("sha256") != local.get("sha256"):
        raise RuntimeError(
            f"golden {version} in S3 (sha256 {remote.get('sha256', '')[:12]}) differs from the repo "
            f"({local.get('sha256', '')[:12]}); bump the manifest version instead of overwriting"
        )
    print(f"golden {version} verified against S3 (sha256 {local['sha256'][:12]})")


def upload_reports(config: StockroomConfig, run_id: str, dry_run: bool = False) -> str:
    bucket = _require_bucket(config)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"{config.s3_prefix}/reports/{stamp}-{run_id}"
    s3 = None if dry_run else _s3(config)
    for name in ("eval_results.json", "promptfoo_results.json", "summary.md"):
        path = REPORTS_DIR / name
        if not path.exists():
            continue
        key = f"{prefix}/{name}"
        print(f"upload {path.relative_to(REPO_ROOT)} -> s3://{bucket}/{key}")
        if s3 is not None:
            s3.upload_file(str(path), bucket, key)
    return prefix


def check_models(config: StockroomConfig) -> None:
    """List the inference profiles / models the configured IDs resolve to (no inference call)."""
    import boto3

    agent, judge = config.require_live_models()
    bedrock = boto3.client("bedrock", region_name=config.aws_region)
    for label, model_id in (("agent", agent), ("judge", judge)):
        try:
            if "." in model_id and model_id.split(".")[0] in {
                "us",
                "eu",
                "apac",
                "global",
                "jp",
                "au",
                "in",
                "ca",
            }:
                info = bedrock.get_inference_profile(inferenceProfileIdentifier=model_id)
                dests = [m["modelArn"].split(":")[3] for m in info.get("models", [])]
                print(
                    f"{label}: {model_id} -> {info.get('status')} destinations {sorted(set(dests))}"
                )
            else:
                info = bedrock.get_foundation_model(modelIdentifier=model_id)
                print(f"{label}: {model_id} -> {info['modelDetails'].get('modelName')}")
        except Exception as exc:  # report, don't hide
            print(f"{label}: {model_id} -> ERROR {exc}")
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("sync-golden", "upload-golden", "check-models"):
        p = sub.add_parser(name)
        p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("download-golden")
    p.add_argument("--version", required=True)
    p.add_argument("--dest", type=Path, default=REPO_ROOT / "reports" / "golden_download")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("upload-reports")
    p.add_argument("--run-id", default="local")
    p.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config = StockroomConfig.from_env()
    dry = getattr(args, "dry_run", False)
    try:
        if args.command == "sync-golden":
            sync_golden(config, dry)
        elif args.command == "upload-golden":
            upload_golden(config, dry)
        elif args.command == "download-golden":
            download_golden(config, args.version, args.dest, dry)
        elif args.command == "upload-reports":
            upload_reports(config, args.run_id, dry)
        elif args.command == "check-models":
            check_models(config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
