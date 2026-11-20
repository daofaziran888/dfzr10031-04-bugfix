"""层级体检业务规则：批次诊断、审计差异留痕与父子边更正后的全图重算。

数据保存在进程内存里（与项目其他模块一致），审计差异同时镜像到数据审计模块
的内存表，便于在"数据审计"页面统一查看。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.hierarchy_graph import (
    CYCLE,
    MISSING_PARENT,
    MISPLACED_CODE,
    HierarchyGraph,
    active_group_keys,
    build_graph,
    diagnose,
)
from app.store import store

AUDIT_MODULE = "audit"
BLOCKING_KINDS = (CYCLE, MISSING_PARENT)
WAIT_VERIFY = "待核验"
VERIFIED = "已核验"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class HierarchyHealthService:
    def __init__(self) -> None:
        self._batches: dict[str, dict[str, Any]] = {}
        self._audits: list[dict[str, Any]] = []
        self._batch_seq = 0
        self._audit_seq = 0

    # ------------------------------------------------------------------ 批次
    def list_batches(self) -> list[dict[str, Any]]:
        return sorted(self._batches.values(), key=lambda item: item["created_at"], reverse=True)

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        return self._batches.get(batch_id)

    def create_batch(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        current = values.get("current") or {}
        if not current.get("nodes") and not current.get("edges"):
            return None, "当期维度数据为空：至少需要 nodes 或 edges 才能体检"
        self._batch_seq += 1
        batch_id = f"CHK-{self._batch_seq:04d}"
        previous = values.get("previous") or None
        report = diagnose(current, previous)
        batch = {
            "batch_id": batch_id,
            "name": str(values.get("name") or f"月度层级体检批次 {self._batch_seq}"),
            "period_current": str(values.get("period_current") or "当期"),
            "period_previous": str(values.get("period_previous") or "上期"),
            "current": _snapshot_payload(current),
            "previous": _snapshot_payload(previous) if previous else None,
            "report": report,
            "corrections": [],
            "created_at": _now(),
        }
        batch["status"] = self._derive_status(report)
        self._batches[batch_id] = batch
        self._raise_audit_differences(batch, report, operator=str(values.get("operator") or "系统体检"))
        return batch, "层级体检完成，已按维度断链影响表生成待核验结账差异"

    # ------------------------------------------------------------------ 审计
    def list_audits(
        self,
        *,
        batch_id: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._audits
        if batch_id:
            rows = [row for row in rows if row.get("batch_id") == batch_id]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows, len(rows)

    def _next_audit_id(self) -> int:
        self._audit_seq += 1
        return self._audit_seq

    def _raise_audit_differences(self, batch: dict[str, Any], report: dict[str, Any], *, operator: str) -> None:
        """按维度断链影响表，为每个受牵连叶子生成一条待核验的结账差异。

        一个叶子挂多个问题组只生成一条记录（按叶子编号去重），依据里保留全部
        问题组、原始上报路径与相关父子边。
        """
        leaf_evidence: dict[str, dict[str, Any]] = {}
        for group in report["groups"]:
            if group["kind"] not in BLOCKING_KINDS:
                continue
            for leaf in group["affected_leaves"]:
                evidence = leaf_evidence.setdefault(leaf["leaf_code"], {
                    "groups": [],
                    "paths": [],
                    "edges": [],
                    "kinds": set(),
                })
                evidence["groups"].append(group["title"])
                evidence["paths"].append(leaf["report_path"])
                evidence["edges"].extend(group["evidence"])
                evidence["kinds"].add(group["kind"])
        blocking_edges = {
            (edge["parent"], edge["child"])
            for group in report["groups"]
            if group["kind"] in BLOCKING_KINDS
            for edge in group["evidence"]
        }
        for leaf_code in sorted(leaf_evidence):
            evidence = leaf_evidence[leaf_code]
            unique_edges = _unique_edges(evidence["edges"])
            entry = self._build_audit_entry(
                batch=batch,
                leaf_code=leaf_code,
                operator=operator,
                action_type="结账差异待核验",
                titles=evidence["groups"],
                paths=evidence["paths"],
                edges=unique_edges,
                kinds=sorted(evidence["kinds"]),
                result=WAIT_VERIFY,
                change="层级体检发现该叶子的上报路径经过闭环或断链，月结金额无法上卷，列入结账差异待核验",
            )
            self._store_audit(entry)
        batch["blocking_edges"] = [{"parent": p, "child": c} for p, c in sorted(blocking_edges)]

    def _build_audit_entry(
        self,
        *,
        batch: dict[str, Any],
        leaf_code: str,
        operator: str,
        action_type: str,
        titles: list[str],
        paths: list[list[str]],
        edges: list[dict[str, str]],
        kinds: list[str],
        result: str,
        change: str,
    ) -> dict[str, Any]:
        audit_id = self._next_audit_id()
        return {
            "id": audit_id,
            "记录编号": f"AUD-H-{audit_id:04d}",
            "batch_id": batch["batch_id"],
            "leaf_code": leaf_code,
            "操作类型": action_type,
            "操作人": operator,
            "操作对象": leaf_code,
            "操作时间": _now(),
            "变更内容": change,
            "审计结果": result,
            "记录状态": result,
            "status": result,
            "pending": result == WAIT_VERIFY,
            "abnormal": result == WAIT_VERIFY,
            "关联问题组": titles,
            "影响类型": "、".join(kinds),
            "上报路径": paths,
            "关联边": edges,
        }

    def _store_audit(self, entry: dict[str, Any]) -> None:
        self._audits.append(entry)
        # 镜像到数据审计模块（同一对象引用，后续核验会自动同步）
        store.rows(AUDIT_MODULE).append(entry)

    # ------------------------------------------------------------ 父子边更正
    def correct_edge(self, batch_id: str, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        batch = self._batches.get(batch_id)
        if batch is None:
            return None, f"体检批次 {batch_id} 不存在"
        old_parent = str(values.get("old_parent") or "").strip()
        child = str(values.get("child") or "").strip()
        new_parent = str(values.get("new_parent") or "").strip()
        if not old_parent or not child or not new_parent:
            return None, "更正需要同时提供 old_parent、child、new_parent"
        target_edge = {"parent": old_parent, "child": child}
        if target_edge not in batch["current"]["edges"]:
            return None, f"父子边 {old_parent} -> {child} 不在当前批次的连接清单中，无法更正"

        new_nodes = [str(node).strip() for node in values.get("new_nodes", []) if str(node).strip()]
        trial = _snapshot_payload(batch["current"])
        trial["nodes"] = sorted(set(trial["nodes"]) | set(new_nodes))
        if new_parent not in trial["nodes"]:
            return None, f"替代上级 {new_parent} 尚未登记，请随 new_nodes 一并上报有效的上级"
        if new_parent == child:
            return None, f"更正会让 {child} 指向自己形成自环，已拦下"

        trial["edges"] = [
            {"parent": new_parent, "child": child} if edge == target_edge else edge
            for edge in trial["edges"]
        ]
        trial_report = diagnose(trial, batch.get("previous"))
        # 更正不能引入新的闭环
        if any(
            group["kind"] == CYCLE and child in group["roots"]
            for group in trial_report["groups"]
        ):
            return None, f"更正后 {child} 仍落入层级闭环，请重新指定替代上级"

        return self._apply_correction(
            batch,
            trial,
            trial_report,
            target_edge,
            {"parent": new_parent, "child": child},
            operator=str(values.get("operator") or "结账值班员"),
            note=str(values.get("note") or ""),
        )

    def _apply_correction(
        self,
        batch: dict[str, Any],
        new_payload: dict[str, Any],
        new_report: dict[str, Any],
        old_edge: dict[str, str],
        new_edge: dict[str, str],
        *,
        operator: str,
        note: str,
    ) -> tuple[dict[str, Any], str]:
        before = batch["report"]
        before_leaves = set(before["summary"]["impacted_leaf_codes"])
        before_paths = _leaf_path_index(before)
        before_groups = active_group_keys(before)

        batch["current"] = new_payload
        batch["report"] = new_report
        batch["status"] = self._derive_status(new_report)

        after_leaves = set(new_report["summary"]["impacted_leaf_codes"])
        after_paths = _leaf_path_index(new_report)
        after_groups = active_group_keys(new_report)
        resolved_groups = [
            title for kind, title in sorted(before_groups - after_groups)
        ]
        released_leaves = sorted(before_leaves - after_leaves)
        # 没被修改的叶子：仍在影响名单里且原始上报路径保持不变
        unmodified_leaves = sorted(
            leaf for leaf in before_leaves & after_leaves
            if before_paths.get(leaf) == after_paths.get(leaf)
        )

        self._verify_related_audits(batch, old_edge, new_edge, released_leaves, operator)

        correction = {
            "at": _now(),
            "operator": operator,
            "old_edge": old_edge,
            "new_edge": new_edge,
            "note": note,
            "resolved_groups": resolved_groups,
            "released_leaves": released_leaves,
            "unmodified_leaves": unmodified_leaves,
            "hanging_subtree": new_report["summary"]["pending_subtree_nodes"],
        }
        batch["corrections"].append(correction)
        return batch, (
            f"父子边已更正并完成全图重算：解挂叶子 {len(released_leaves)} 个，"
            f"仍挂起子树 {new_report['summary']['pending_subtree_count']} 个节点"
        )

    def _verify_related_audits(
        self,
        batch: dict[str, Any],
        old_edge: dict[str, str],
        new_edge: dict[str, str],
        released_leaves: list[str],
        operator: str,
    ) -> None:
        """按父子边更正重新计算关联审计记录：解挂叶子的差异转为已核验。"""
        released = set(released_leaves)
        old_pair = (old_edge["parent"], old_edge["child"])
        for entry in self._audits:
            if entry["batch_id"] != batch["batch_id"] or entry["status"] != WAIT_VERIFY:
                continue
            related = any((edge["parent"], edge["child"]) == old_pair for edge in entry["关联边"])
            if related and entry["leaf_code"] in released:
                entry["status"] = VERIFIED
                entry["记录状态"] = VERIFIED
                entry["审计结果"] = VERIFIED
                entry["pending"] = False
                entry["abnormal"] = False
                entry["操作时间"] = _now()
                entry["变更内容"] = (
                    f"父子边 {old_edge['parent']}->{old_edge['child']} 更正为 "
                    f"{new_edge['parent']}->{new_edge['child']} 后重算，叶子已解挂，差异核验通过"
                )
        # 更正动作本身留一条审计
        record = {
            "id": self._next_audit_id(),
            "记录编号": f"AUD-H-{self._audit_seq:04d}",
            "batch_id": batch["batch_id"],
            "leaf_code": "",
            "操作类型": "父子边更正",
            "操作人": operator,
            "操作对象": f"{old_edge['child']}",
            "操作时间": _now(),
            "变更内容": (
                f"边 {old_edge['parent']}->{old_edge['child']} 更正为 "
                f"{new_edge['parent']}->{new_edge['child']}，全图挂起子树已重新计算"
            ),
            "审计结果": VERIFIED,
            "记录状态": VERIFIED,
            "status": VERIFIED,
            "pending": False,
            "abnormal": False,
            "关联问题组": [],
            "影响类型": "边更正",
            "上报路径": [],
            "关联边": [old_edge, new_edge],
        }
        self._store_audit(record)

    # ------------------------------------------------------------------ 辅助
    @staticmethod
    def _derive_status(report: dict[str, Any]) -> str:
        summary = report["summary"]
        if summary["impacted_leaf_count"] == 0 and summary["cycle_count"] == 0 and summary["missing_parent_count"] == 0:
            return "体检通过"
        return "存在结账异常"


def _snapshot_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """把接口报文规整成可持久修改的快照（节点去重、边统一成字典）。"""
    payload = payload or {}
    graph = build_graph(payload)
    nodes = sorted(graph.known_nodes) if hasattr(graph, "known_nodes") else sorted(graph.nodes)
    return {
        "nodes": nodes,
        "edges": [{"parent": parent, "child": child} for parent, child in graph.edges],
    }


def _unique_edges(edges: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for edge in edges:
        pair = (edge["parent"], edge["child"])
        if pair not in seen:
            seen.add(pair)
            result.append(edge)
    return result


def _leaf_path_index(report: dict[str, Any]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for group in report["groups"]:
        if group["kind"] not in BLOCKING_KINDS:
            continue
        for leaf in group["affected_leaves"]:
            index.setdefault(leaf["leaf_code"], leaf["report_path"])
    return index
