"""维度层级图诊断引擎（只依赖标准库）。

面向月度地区等级结账前的层级体检，回答三类问题：

1. 层级闭环：父子边构成的最小闭环（自环算一个节点的闭环，两个节点互指算
   两个节点的闭环），闭环上的边必须原样保留；
2. 父级丢失：子节点仍在当期维度里，父节点却在两期维度中都不存在（断链边）；
3. 代码错位：节点两期都在，但上报的父级发生了变化（旧父级当期仍存在）。

同时给出每个问题组牵连到的叶子及其"原始上报路径"。一个叶子挂到多个问题组
时，影响数量按叶子编号去重。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

# 问题组类型
CYCLE = "层级闭环"
MISSING_PARENT = "父级丢失"
MISPLACED_CODE = "代码错位"


def _as_text(value: Any) -> str:
    """节点编号统一成字符串，兼容调用方传数字的情况。"""
    if value is None:
        return ""
    return str(value).strip()


class HierarchyGraph:
    """一期维度数据对应的父子有向图。

    边方向约定为 ``父 -> 子``（父级指向自己的下级）；叶子是没有出边的节点。
    """

    def __init__(
        self,
        nodes: Iterable[Any] | None = None,
        edges: Iterable[Any] | None = None,
    ) -> None:
        self.nodes: set[str] = set()
        self.edges: list[tuple[str, str]] = []
        self._edge_set: set[tuple[str, str]] = set()
        self.children: dict[str, list[str]] = defaultdict(list)
        self.parents: dict[str, list[str]] = defaultdict(list)
        for node in nodes or []:
            self.add_node(node)
        for edge in edges or []:
            self.add_edge(edge)

    # ------------------------------------------------------------------ 构造
    def add_node(self, node: Any) -> str:
        node_id = node["code"] if isinstance(node, dict) else node
        node_id = _as_text(node_id)
        if node_id:
            self.nodes.add(node_id)
        return node_id

    def add_edge(self, edge: Any) -> tuple[str, str] | None:
        if isinstance(edge, dict):
            parent = _as_text(edge.get("parent") or edge.get("parent_code") or edge.get("父级编号"))
            child = _as_text(edge.get("child") or edge.get("child_code") or edge.get("子级编号"))
        elif isinstance(edge, (list, tuple)) and len(edge) >= 2:
            parent, child = _as_text(edge[0]), _as_text(edge[1])
        else:
            return None
        if not parent or not child:
            return None
        pair = (parent, child)
        if pair in self._edge_set:
            return pair
        self._edge_set.add(pair)
        self.edges.append(pair)
        # 端点即便没在 nodes 里登记也补进来，丢失的父级同样进入图中参与遍历
        self.nodes.add(parent)
        self.nodes.add(child)
        self.children[parent].append(child)
        self.parents[child].append(parent)
        return pair

    # ------------------------------------------------------------------ 基本量
    def is_leaf(self, node_id: str) -> bool:
        return not self.children.get(node_id)

    def leaves(self) -> set[str]:
        return {node_id for node_id in self.nodes if self.is_leaf(node_id)}

    def downstream(self, roots: Iterable[str]) -> set[str]:
        """沿 ``父->子`` 方向求 roots 能到达的全部节点（含 roots 自身）。"""
        reached: set[str] = set()
        stack = [root for root in roots if root]
        while stack:
            current = stack.pop()
            if current in reached:
                continue
            reached.add(current)
            stack.extend(self.children.get(current, ()))
        return reached

    def report_path(self, leaf: str, stop_nodes: Iterable[str] = (), *, stop_at_roots: bool = True) -> list[str]:
        """叶子的原始上报路径：从叶子一路向上追到根（或闭环入口）。

        图里有多个父级时按登记顺序回溯；遇到重复节点即停，保证闭环上的路径
        不会无限绕圈。``stop_nodes`` 默认同时作为提前停止点（闭环组在环入口
        停住）；断链组需要继续追到丢失的父级，可传 ``stop_at_roots=False``。
        """
        stop = set(stop_nodes) if stop_at_roots else set()
        path = [leaf]
        seen = {leaf}
        current = leaf
        while self.parents.get(current):
            nxt = self.parents[current][0]
            path.append(nxt)
            if nxt in stop or nxt in seen:
                break
            seen.add(nxt)
            current = nxt
        return path

    # ---------------------------------------------------------- Tarjan 最小闭环
    def find_cycles(self) -> list[dict[str, Any]]:
        """找出全部最小闭环（强连通分量），自环与两节点互指都能识别。"""
        index_counter = 0
        stack: list[str] = []
        on_stack: set[str] = set()
        indices: dict[str, int] = {}
        low_links: dict[str, int] = {}
        components: list[list[str]] = []

        def strong_connect(node: str) -> None:
            nonlocal index_counter
            indices[node] = index_counter
            low_links[node] = index_counter
            index_counter += 1
            stack.append(node)
            on_stack.add(node)
            for child in self.children.get(node, ()):
                if child not in indices:
                    strong_connect(child)
                    low_links[node] = min(low_links[node], low_links[child])
                elif child in on_stack:
                    low_links[node] = min(low_links[node], indices[child])
            if low_links[node] == indices[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)

        # 固定遍历顺序，输出稳定可核对
        for node in sorted(self.nodes):
            if node not in indices:
                strong_connect(node)

        cycles: list[dict[str, Any]] = []
        for component in components:
            member_set = set(component)
            internal_edges = [
                {"parent": parent, "child": child}
                for parent, child in self.edges
                if parent in member_set and child in member_set
            ]
            # 单点 SCC 只有带自环才算闭环
            if len(component) == 1 and not internal_edges:
                continue
            members = sorted(component)
            cycles.append({
                "cycle_id": f"CYCLE-{len(cycles) + 1:03d}",
                "kind": CYCLE,
                "nodes": members,
                # 跟踪到环时保留环上的边
                "edges": internal_edges,
                "entry": members[0],
            })
        return cycles

    # ---------------------------------------------------------------- 父级丢失
    def find_missing_parents(self) -> list[dict[str, Any]]:
        """断链边：父节点在两期维度中都没有登记（由调用方告知已知节点集合）。"""
        return [
            {
                "kind": MISSING_PARENT,
                "parent": parent,
                "child": child,
                "edge": {"parent": parent, "child": child},
            }
            for parent, child in self.edges
            if parent not in self.known_nodes
        ]

    # ---------------------------------------------------------------- 代码错位
    def find_misplaced_codes(self, previous: "HierarchyGraph | None") -> list[dict[str, Any]]:
        """两期都在的节点，父级集合变了且旧父级当期仍在，判为代码错位。"""
        if previous is None:
            return []
        results: list[dict[str, Any]] = []
        for node in sorted(self.nodes & previous.nodes):
            old_parents = set(previous.parents.get(node, ()))
            new_parents = set(self.parents.get(node, ()))
            dropped = old_parents - new_parents
            # 旧父级当期仍存在，却不再挂这个子节点：典型的代码错位
            for old_parent in sorted(dropped):
                if old_parent in self.nodes:
                    results.append({
                        "kind": MISPLACED_CODE,
                        "node": node,
                        "previous_parent": old_parent,
                        "current_parents": sorted(new_parents),
                        "edge": {"parent": old_parent, "child": node},
                    })
        return results


def build_graph(payload: dict[str, Any] | None) -> HierarchyGraph:
    """从接口报文构造一期层级图。

    报文支持两种节点写法：``[{code: ...}]`` 或 ``["C1", "C2"]``；
    边支持 ``[{parent, child}]`` 或 ``[["P", "C"]]``。
    """
    payload = payload or {}
    graph = HierarchyGraph()
    known: set[str] = set()
    for raw in payload.get("nodes", []) or []:
        code = graph.add_node(raw)
        if code:
            known.add(code)
    for raw in payload.get("edges", []) or []:
        graph.add_edge(raw)
    # known_nodes 只包含真正在维度清单里登记过的节点，断链的父级不在其中
    graph.known_nodes = known  # type: ignore[attr-defined]
    return graph


def _issue_group(
    seq: int,
    kind: str,
    title: str,
    roots: list[str],
    graph: HierarchyGraph,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """组装一个问题组：受牵连叶子 + 每个叶子的原始上报路径。"""
    affected_nodes = graph.downstream(roots)
    affected_leaves = sorted(node_id for node_id in affected_nodes if graph.is_leaf(node_id))
    leaves_detail = [
        {
            "leaf_code": leaf,
            "report_path": graph.report_path(
                leaf, stop_nodes=roots, stop_at_roots=kind != MISSING_PARENT
            ),
        }
        for leaf in affected_leaves
    ]
    return {
        "group_id": f"{kind[:2]}-{seq:03d}",
        "kind": kind,
        "title": title,
        "roots": sorted(set(roots)),
        "nodes": sorted(set(roots)),
        "evidence": evidence,
        "affected_nodes": sorted(affected_nodes),
        "affected_leaves": leaves_detail,
        "leaf_codes": affected_leaves,
        "impact_count": len(affected_leaves),
    }


def diagnose(
    current_payload: dict[str, Any],
    previous_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对两期维度数据做一次完整体检，返回问题组、影响面与两期变动。"""
    current = build_graph(current_payload)
    previous = build_graph(previous_payload) if previous_payload else None
    if not hasattr(current, "known_nodes"):
        current.known_nodes = set(current.nodes)  # type: ignore[attr-defined]

    groups: list[dict[str, Any]] = []

    # 1) 层级闭环：每个强连通分量一个问题组
    for seq, cycle in enumerate(current.find_cycles(), start=1):
        groups.append(_issue_group(
            seq,
            CYCLE,
            f"层级闭环：{'->'.join(cycle['nodes'])} 形成运算报错的最小闭环",
            cycle["nodes"],
            current,
            cycle["edges"],
        ))

    # 2) 父级丢失：每条断链边一个问题组，根是失去上级的子节点
    for seq, broken in enumerate(current.find_missing_parents(), start=1):
        groups.append(_issue_group(
            seq,
            MISSING_PARENT,
            f"父级丢失：{broken['child']} 的上级 {broken['parent']} 在两期维度中均不存在",
            [broken["child"]],
            current,
            [broken["edge"]],
        ))

    # 3) 代码错位：每个错位节点一个问题组（警告类，不计入结账阻断影响面）
    for seq, misplaced in enumerate(current.find_misplaced_codes(previous), start=1):
        groups.append(_issue_group(
            seq,
            MISPLACED_CODE,
            f"代码错位：{misplaced['node']} 的上级由 {misplaced['previous_parent']} 调整",
            [misplaced["node"]],
            current,
            [misplaced["edge"]],
        ))

    blocking_kinds = {CYCLE, MISSING_PARENT}
    # 一条叶子同时挂到多个问题组：影响数量按叶子编号去重
    impacted_leaves: set[str] = set()
    blocking_nodes: set[str] = set()
    for group in groups:
        if group["kind"] in blocking_kinds:
            impacted_leaves.update(group["leaf_codes"])
            blocking_nodes.update(group["affected_nodes"])

    period_change = _diff_periods(previous, current)
    return {
        "groups": groups,
        "summary": {
            "cycle_count": sum(1 for g in groups if g["kind"] == CYCLE),
            "missing_parent_count": sum(1 for g in groups if g["kind"] == MISSING_PARENT),
            "misplaced_count": sum(1 for g in groups if g["kind"] == MISPLACED_CODE),
            # 待定子树（闭环/断链阻断的全部节点，含非叶子）计入影响结账覆盖
            "pending_subtree_nodes": sorted(blocking_nodes),
            "pending_subtree_count": len(blocking_nodes),
            "impacted_leaf_codes": sorted(impacted_leaves),
            "impacted_leaf_count": len(impacted_leaves),
        },
        "period_change": period_change,
    }


def _diff_periods(previous: HierarchyGraph | None, current: HierarchyGraph) -> dict[str, Any]:
    """两期维度与父子边的增删改，供审计日志留痕。"""
    if previous is None:
        return {
            "nodes_added": sorted(current.nodes),
            "nodes_removed": [],
            "edges_added": [{"parent": p, "child": c} for p, c in current.edges],
            "edges_removed": [],
        }
    prev_edges = set(previous.edges)
    cur_edges = set(current.edges)
    return {
        "nodes_added": sorted(current.nodes - previous.nodes),
        "nodes_removed": sorted(previous.nodes - current.nodes),
        "edges_added": [{"parent": p, "child": c} for p, c in sorted(cur_edges - prev_edges)],
        "edges_removed": [{"parent": p, "child": c} for p, c in sorted(prev_edges - cur_edges)],
    }


def active_group_keys(report: dict[str, Any]) -> set[tuple[str, str]]:
    """当前报告里仍成立的问题组标识：(问题类型, 组标题)。"""
    return {(group["kind"], group["title"]) for group in report.get("groups", [])}
