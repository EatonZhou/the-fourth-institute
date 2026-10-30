from unittest import mock

import pytest

from management_portal import paper_detail, paper_store


def test_paper_asset_paths_cannot_escape_the_registered_bundle(tmp_path):
    """论文文件与图表路径指向登记目录之外时解析为不存在"""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    inside = bundle / "inside.png"
    inside.write_bytes(b"inside")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    store = paper_store.PaperStore(tmp_path / "paper-store")
    paper = {
        "id": "paper_0123456789ab",
        "_store_root": str(bundle),
        "_files": {"pdf": "inside.png"},
    }

    assert store.paper_file(paper, "pdf") == inside.resolve()
    paper["_files"]["pdf"] = "../outside.png"
    assert store.paper_file(paper, "pdf") is None
    paper["_files"]["pdf"] = str(outside)
    assert store.paper_file(paper, "pdf") is None

    with (
        mock.patch.object(paper_detail, "_paper_for_asset_lookup", return_value=paper),
        mock.patch.object(
            paper_detail,
            "_load_figures",
            return_value=[{"figure_id": "figure_one", "image_rel_path": "../outside.png"}],
        ),
        pytest.raises(FileNotFoundError, match="figure"),
    ):
        paper_detail.resolve_asset(paper["id"], "figure", "figure_one")




