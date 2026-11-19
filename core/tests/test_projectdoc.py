"""项目文档：需求 / 决策 / 变更 走同一份文件。"""

from __future__ import annotations

from pathlib import Path

from oryx.projectdoc import ProjectDoc


def test_creates_single_document(tmp_path: Path):
    doc = ProjectDoc(tmp_path)
    assert not doc.exists()

    entry_id = doc.add_requirement("支持手机远控", "输入配对码即可操作桌面")
    assert entry_id == "REQ-001"

    assert doc.exists()
    assert doc.path.name == "PROJECT.md"
    text = doc.read()
    assert "# 项目文档" in text
    assert "REQ-001" in text
    assert "支持手机远控" in text


def test_ids_increment(tmp_path: Path):
    doc = ProjectDoc(tmp_path)
    assert doc.add_requirement("A") == "REQ-001"
    assert doc.add_requirement("B") == "REQ-002"
    assert doc.add_decision("选 Tauri") == "DEC-001"
    assert doc.log_change("加了远控") == "CHG-001"
    assert doc.log_change("修了 bug") == "CHG-002"


def test_change_records_files_and_rationale(tmp_path: Path):
    doc = ProjectDoc(tmp_path)
    doc.log_change("新增用量统计", files=["core/store.py", "core/loop.py"], rationale="老板要看消耗")
    text = doc.read()
    assert "core/store.py" in text
    assert "老板要看消耗" in text
    assert "CHG-001" in text


def test_sections_stay_separate(tmp_path: Path):
    doc = ProjectDoc(tmp_path)
    doc.add_requirement("需求一")
    doc.add_decision("决策一")
    doc.add_requirement("需求二")
    doc.log_change("改动一")

    text = doc.read()
    req_part = text.split("## 需求")[1].split("## 决策")[0]
    dec_part = text.split("## 决策")[1].split("## 变更记录")[0]
    chg_part = text.split("## 变更记录")[1]

    assert "需求二" in req_part and "需求一" in req_part
    assert "决策一" in dec_part and "需求" not in dec_part
    assert "改动一" in chg_part


def test_stats(tmp_path: Path):
    doc = ProjectDoc(tmp_path)
    doc.add_requirement("R")
    doc.log_change("C")
    stats = doc.stats()
    assert stats["requirements"] == 1
    assert stats["changes"] == 1
    assert stats["exists"] is True
    assert stats["bytes"] > 0


def test_read_limit(tmp_path: Path):
    doc = ProjectDoc(tmp_path)
    for i in range(20):
        doc.add_requirement(f"需求{i}", "细节" * 50)
    assert len(doc.read(max_chars=200)) == 200
