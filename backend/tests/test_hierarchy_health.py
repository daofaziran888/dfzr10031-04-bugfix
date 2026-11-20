"""层级体检端到端测试：覆盖题目要求的同批场景。

场景（边方向统一为 父 -> 子）：
- 自环 S->S
- 两节点互指 A<->B，并在 A 下挂叶子 LA（用于核对没被修改的叶子）
- 断链 G->M：原上级 G 在两期维度中都不存在，M 下挂两个共享坏祖先的叶子 L1/L2
- 代码错位 N 的上级由 P0 改为 R（旧上级仍在）
- 更正：把 G->M 改挂到迟到的有效上级 H（随更正补登记）

直接跑：python -m tests.test_hierarchy_health（在 backend 目录下）
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.hierarchy_graph import CYCLE, MISSING_PARENT, MISPLACED_CODE, diagnose

client = TestClient(app)

PREVIOUS = {
    "nodes": ["A", "B", "LA", "M", "L1", "L2", "N", "P0", "R"],
    "edges": [
        {"parent": "P0", "child": "N"},
        {"parent": "A", "child": "LA"},
        {"parent": "M", "child": "L1"},
        {"parent": "M", "child": "L2"},
    ],
}

CURRENT = {
    # G 故意不在 nodes 里：原上级被撤掉、替代上级迟到
    "nodes": ["S", "A", "B", "LA", "M", "L1", "L2", "N", "P0", "R"],
    "edges": [
        {"parent": "S", "child": "S"},          # 自环
        {"parent": "A", "child": "B"},          # 两节点互指
        {"parent": "B", "child": "A"},
        {"parent": "A", "child": "LA"},         # 挂在坏祖先 A 下的叶子
        {"parent": "G", "child": "M"},          # 父级丢失（G 未登记）
        {"parent": "M", "child": "L1"},
        {"parent": "M", "child": "L2"},
        {"parent": "R", "child": "N"},          # N 代码错位（旧父 P0 仍在）
    ],
}


def assert_equal(actual, expected, label: str) -> None:
    assert actual == expected, f"[{label}] 期望 {expected!r}，实际 {actual!r}"
    print(f"  ✓ {label}: {actual!r}")


def test_engine_smoke() -> None:
    print("== 纯引擎体检 ==")
    report = diagnose(CURRENT, PREVIOUS)
    kinds = {group["kind"] for group in report["groups"]}
    assert_equal(kinds, {CYCLE, MISSING_PARENT, MISPLACED_CODE}, "识别出三类问题")

    cycle_nodes = sorted(
        tuple(sorted(group["nodes"]))
        for group in report["groups"] if group["kind"] == CYCLE
    )
    assert_equal(cycle_nodes, [("A", "B"), ("S",)], "最小闭环为自环 S 与互指环 A/B")

    # 环上的边必须保留
    edge_lookup = {tuple(sorted(group["nodes"])): group["evidence"]
                   for group in report["groups"] if group["kind"] == CYCLE}
    assert_equal(edge_lookup[("S",)], [{"parent": "S", "child": "S"}], "自环边保留")
    assert_equal(
        sorted((e["parent"], e["child"]) for e in edge_lookup[("A", "B")]),
        [("A", "B"), ("B", "A")],
        "互指环上两条边保留",
    )

    summary = report["summary"]
    assert_equal(summary["missing_parent_count"], 1, "一条断链边")
    assert_equal(summary["misplaced_count"], 1, "一条代码错位")
    # 叶子编号去重：L1/L2/LA 各算一次（自环节点 S 出度为 1，不是叶子）
    assert_equal(summary["impacted_leaf_codes"], ["L1", "L2", "LA"], "受牵连叶子按编号去重")
    assert_equal(summary["impacted_leaf_count"], 3, "影响数量为 3")
    # 待定子树：闭环节点 + 环下叶子 + 断链下游（丢失的 G 在上游，不计入子树）
    assert_equal(
        summary["pending_subtree_nodes"],
        ["A", "B", "L1", "L2", "LA", "M", "S"],
        "待定子树计入影响结账覆盖（7 个节点）",
    )
    assert_equal(summary["pending_subtree_count"], 7, "待定子树节点数为 7")

    # 原始上报路径保留
    missing_group = next(g for g in report["groups"] if g["kind"] == MISSING_PARENT)
    path_map = {leaf["leaf_code"]: leaf["report_path"] for leaf in missing_group["affected_leaves"]}
    assert_equal(path_map["L1"], ["L1", "M", "G"], "L1 原始上报路径保留")
    assert_equal(path_map["L2"], ["L2", "M", "G"], "L2 原始上报路径保留")

    cycle_group = next(g for g in report["groups"] if g["kind"] == CYCLE and g["nodes"] == ["A", "B"])
    la = next(leaf for leaf in cycle_group["affected_leaves"] if leaf["leaf_code"] == "LA")
    assert_equal(la["report_path"], ["LA", "A"], "环上叶子 LA 的上报路径在闭环入口 A 处停住")

    misplaced = next(g for g in report["groups"] if g["kind"] == MISPLACED_CODE)
    assert_equal(misplaced["leaf_codes"], ["N"], "错位节点 N 作为叶子单列，且不并入结账阻断名单")


def test_api_flow() -> None:
    print("== 接口：建批次 + 审计差异 ==")
    resp = client.post("/api/hierarchy_health/batches", json={
        "name": "11月地区等级结账前层级体检",
        "period_current": "2026-11",
        "period_previous": "2026-10",
        "operator": "结账值班员",
        "current": CURRENT,
        "previous": PREVIOUS,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    batch = body["entry"]
    batch_id = batch["batch_id"]
    print(f"  ✓ 批次创建：{batch_id}，状态 {batch['status']}")
    assert_equal(batch["status"], "存在结账异常", "批次标记为结账异常")

    # 结账异常名单
    assert_equal(
        batch["report"]["summary"]["impacted_leaf_codes"], ["L1", "L2", "LA"],
        "结账异常名单（按叶子编号去重）",
    )

    # 审计日志：维度断链影响表 -> 待核验结账差异
    resp = client.get(f"/api/hierarchy_health/audits?batch_id={batch_id}")
    audits = resp.json()["items"]
    waiting = [row for row in audits if row["status"] == "待核验"]
    assert_equal(len(waiting), 3, "为 L1/L2/LA 各生成一条待核验差异（去重）")
    assert_equal(
        sorted(row["leaf_code"] for row in waiting), ["L1", "L2", "LA"],
        "审计差异按叶子编号",
    )
    l1 = next(row for row in waiting if row["leaf_code"] == "L1")
    assert l1["关联边"] and l1["上报路径"], "审计依据保留关联边与上报路径"
    print(f"  ✓ L1 审计依据：边 {l1['关联边']}，路径 {l1['上报路径']}")

    print("== 接口：迟到有效上级的父子边更正 ==")
    # 先验证：替代上级未登记时必须拦下
    resp = client.post(f"/api/hierarchy_health/batches/{batch_id}/corrections", json={
        "old_parent": "G", "child": "M", "new_parent": "H",
        "operator": "结账值班员",
    })
    assert resp.json()["ok"] is False
    print("  ✓ 未登记的替代上级 H 被拦下")

    # 自环更正也必须被拦下
    resp = client.post(f"/api/hierarchy_health/batches/{batch_id}/corrections", json={
        "old_parent": "S", "child": "S", "new_parent": "S",
        "operator": "结账值班员",
    })
    assert resp.json()["ok"] is False
    print("  ✓ 指向自己的自环更正被拦下")

    # 随更正补登记 H，原上级 G 撤掉后的替代上级迟到
    resp = client.post(f"/api/hierarchy_health/batches/{batch_id}/corrections", json={
        "old_parent": "G",
        "child": "M",
        "new_parent": "H",
        "new_nodes": ["H"],
        "operator": "结账值班员",
        "note": "原上级 G 已撤销，迟到上级 H 接管子树",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True, body["message"]
    updated = body["entry"]
    correction = updated["corrections"][-1]

    # 已解挂范围
    assert_equal(correction["released_leaves"], ["L1", "L2"], "已解挂叶子范围")
    # 没被修改的叶子：LA 仍在异常名单且上报路径不变
    assert_equal(correction["unmodified_leaves"], ["LA"], "没被修改的叶子 LA")
    assert_equal(
        updated["report"]["summary"]["impacted_leaf_codes"], ["LA"],
        "更正后剩余结账异常名单",
    )
    # 仍挂起的子树（全图重算）
    assert_equal(
        updated["report"]["summary"]["pending_subtree_nodes"],
        ["A", "B", "LA", "S"],
        "全图重算后仍挂起的子树",
    )

    # 审计记录联动核验
    resp = client.get(f"/api/hierarchy_health/audits?batch_id={batch_id}")
    audits = resp.json()["items"]
    by_leaf = {row["leaf_code"]: row for row in audits if row["leaf_code"] in {"L1", "L2", "LA"}}
    assert_equal(by_leaf["L1"]["status"], "已核验", "L1 关联差异按边更正后核验")
    assert_equal(by_leaf["L2"]["status"], "已核验", "L2 关联差异按边更正后核验")
    assert_equal(by_leaf["LA"]["status"], "待核验", "LA 未被本次修改，差异仍挂起")
    edge_audit = next(row for row in audits if row["操作类型"] == "父子边更正")
    assert_equal(
        edge_audit["关联边"],
        [{"parent": "G", "child": "M"}, {"parent": "H", "child": "M"}],
        "更正审计保留新旧父子边依据",
    )

    # 叶子本身没有被修改：L1/L2 的节点与其余连接保持原样
    current_after = updated["current"]
    assert {"parent": "M", "child": "L1"} in current_after["edges"], "L1 原有连接未被修改"
    assert {"parent": "M", "child": "L2"} in current_after["edges"], "L2 原有连接未被修改"
    assert all(code in current_after["nodes"] for code in ("L1", "L2", "LA")), "叶子节点均未被修改"
    assert {"parent": "H", "child": "M"} in current_after["edges"], "仅更正为 H->M"
    assert {"parent": "G", "child": "M"} not in current_after["edges"], "旧断链边已移除"
    print("  ✓ L1/L2/LA 节点及其余父子边均未被修改，只更正了 G->M 这一条边")

    # 数据审计主表也能看到镜像记录
    resp = client.get("/api/audit?size=200")
    mirrored = [row for row in resp.json()["items"] if str(row.get("记录编号", "")).startswith("AUD-H-")]
    assert mirrored, "结账差异已镜像到数据审计模块"
    print(f"  ✓ 数据审计模块可见 {len(mirrored)} 条层级体检审计记录")


def main() -> None:
    test_engine_smoke()
    test_api_flow()
    print("\n全部断言通过 ✅")


if __name__ == "__main__":
    main()
