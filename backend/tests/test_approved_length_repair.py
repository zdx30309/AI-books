import json

import pytest

from app.services.general_short_length import (
    LengthHold, apply_approved_length_reduction, digest, load_approved_length_repair,
    manuscript_parts, repair_length,
)


def book(sizes):
    return "@最终书名\n测试书\n@目录\n" + "\n".join(f"@{number}. 章名" for number in range(1, 10)) + \
           "\n@导言\n说明\n@正文\n" + "\n\n".join(f"@{number}. 章名\n" + "文" * size for number, size in enumerate(sizes, 1))


def fixture(tmp_path):
    initial = book([700] * 9)
    base = book([2550] * 7 + [3380, 1119])
    response = "压" * 2400
    result = apply_approved_length_reduction(base, response, 8)
    directory = tmp_path / "approved_length_repair"
    directory.mkdir()
    for name, text in (("base.md", base), ("response.md", response), ("result.md", result)):
        (directory / name).write_text(text)
    record = {"status": "approved", "human_approval": "用户批准在累计10元内局部压缩并续跑",
              "chapter_number": 8, "initial_sha256": digest(initial), "source_sha256": digest("参考资料"),
              "base_sha256": digest(base), "response_sha256": digest(response), "result_sha256": digest(result)}
    (directory / "approval.json").write_text(json.dumps(record))
    return initial, base, result, directory, record


def test_only_one_chapter_changes_and_resume_has_no_paid_calls(tmp_path, monkeypatch):
    from app.services import deepseek

    initial, base, result, directory, record = fixture(tmp_path)
    before, after = manuscript_parts(base), manuscript_parts(result)
    assert all(before[index] == after[index] for index in range(9) if index != 7)
    monkeypatch.setattr(deepseek, "generate_text", lambda *args, **kwargs: pytest.fail("批准稿不能重新付费补写"))
    assert repair_length(initial, "参考资料", tmp_path, approved_sha256=digest(result)) == result


@pytest.mark.parametrize("field", ["initial_sha256", "source_sha256", "base_sha256", "response_sha256", "result_sha256", "human_approval"])
def test_bound_inputs_and_approval_cannot_change(tmp_path, field):
    initial, base, result, directory, record = fixture(tmp_path)
    record[field] = "changed" if field != "human_approval" else ""
    (directory / "approval.json").write_text(json.dumps(record))
    with pytest.raises(LengthHold):
        load_approved_length_repair(initial, "参考资料", tmp_path, digest(result))


def test_changing_another_chapter_is_not_a_local_repair(tmp_path):
    initial, base, result, directory, record = fixture(tmp_path)
    result = result.replace("文", "另", 1)
    record["result_sha256"] = digest(result)
    (directory / "result.md").write_text(result)
    (directory / "approval.json").write_text(json.dumps(record))
    with pytest.raises(LengthHold):
        load_approved_length_repair(initial, "参考资料", tmp_path, digest(result))


def test_short_global_body_is_still_rejected():
    with pytest.raises(LengthHold):
        apply_approved_length_reduction(book([700] * 7 + [3380, 1119]), "压" * 2400, 8)


def test_successful_followup_does_not_overwrite_original_response(tmp_path):
    initial, base, result, directory, record = fixture(tmp_path)
    accepted = (directory / "response.md").read_text()
    (directory / "accepted_response.md").write_text(accepted)
    (directory / "response.md").write_text("已保存的第一次失败响应")
    record["response_filename"] = "accepted_response.md"
    (directory / "approval.json").write_text(json.dumps(record))
    assert load_approved_length_repair(initial, "参考资料", tmp_path, digest(result)) == result
    assert (directory / "response.md").read_text() == "已保存的第一次失败响应"
