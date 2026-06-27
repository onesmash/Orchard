# Process Quality + Community Precision + UX 优化设计

## P1: Process 入口点评分优化

### 现状

```
6/75 processes match known flow patterns
getter:body 出现 3 次，dealloc 出现 2 次
C++ operator-heavy entries 占多数
```

### 优化方案

入口点评分从单一 ratio 改为加权多因素：

```python
ENTRY_PATTERNS = {
    # 高价值（×3）：系统委托、通知处理器
    r"^(application|scene|userNotificationCenter):": 3.0,
    r"^(handle|Handle|didReceive|onReceive)": 2.5,
    r"^(imCmd|conf|noti|call)": 2.0,
    r"^(viewDid|onLogin|onStart|onConf|push)": 1.5,
}

ENTRY_BLACKLIST = {
    # 低价值：访问器、析构器、UI delegate 重复
    r"^(getter:|setter:|dealloc|init$|initWith)": 0,
    r"^(tableView:|collectionView:|numberOf)": 0,
    r"^(itemsWith|actionsWith|onRender|onMoreMenu)": 0,
}

def score_entry(name, kind, callee_count, caller_count):
    if any(re.match(p, name) for p in ENTRY_BLACKLIST):
        return 0
    ratio = callee_count / (caller_count + 1)
    boost = 1.0
    for pattern, weight in ENTRY_PATTERNS.items():
        if re.search(pattern, name):
            boost = max(boost, weight)
    return ratio * boost
```

预期：6/75 → 30+/75。`application:didFinishLaunchingWithOptions:` 得分 = ratio × 3.0，排进前 5。`getter:body` 得分 = 0，被过滤。

---

## P2: Leiden 算法替换 Label Propagation

### 现状

Label Propagation 在 67K 节点上产生 giant component（90% 符号在一个社区）。Leiden 算法保证模块度最优，不会产生 giant component。

### 方案

不引入 C++ 依赖。用 Python `leidenalg` + `igraph`：

```bash
pip install leidenalg igraph
```

```python
def run_community_detection(conn, target_id):
    import igraph as ig
    import leidenalg

    # 1. 加载边到 igraph
    adj = _build_adjacency(conn)  # 复用现有逻辑
    g = ig.Graph()
    usr_to_idx = {}
    for usr in adj:
        usr_to_idx[usr] = len(usr_to_idx)
    g.add_vertices(len(usr_to_idx))
    edges = []
    for src, targets in adj.items():
        for tgt in targets:
            edges.append((usr_to_idx[src], usr_to_idx[tgt]))
    g.add_edges(edges)

    # 2. Leiden 分区
    partition = leidenalg.find_partition(
        g, leidenalg.ModularityVertexPartition,
        n_iterations=2, seed=0xc0de,
    )

    # 3. CSV batch write
    _write_communities_csv(conn, target_id, partition, usr_to_idx)
```

预计：社区分布均匀（不再有 90% giant component），处理时间 ~5s（igraph 是 C 库）。向后兼容：`--communities` flag 行为不变。

---

## P3: 用户体验

### `orchard process` 输出改进

当前：
```json
{"count": 75, "processes": [{"id": "proc_Zoom_0", "entry_name": "getter:body", "entry_kind": "method"}, ...]}
```

改为：
```json
{
  "count": 75,
  "processes": [
    {
      "id": "proc_Zoom_2",
      "entry_name": "application:didFinishLaunchingWithOptions:",
      "entry_kind": "method",
      "label": "application:didFinishLaunchingWithOptions: → loginStatus",
      "process_type": "cross_community",
      "step_count": 693,
      "communities": ["community:Zoom:1542"]
    }
  ]
}
```

### 新增 `orchard process show <id>`

```bash
$ orchard process show proc_Zoom_2
application:didFinishLaunchingWithOptions: (entry)
  → tryAutoLoginWhenAppLaunched                  (step 1)
    → setNeedResetPreviousOrderAfterAutoLogin:   (step 2)
    → needResetPreviousOrderAfterAutoLogin       (step 3)
    ...
    → getter:loginStatus                         (step 693)
```

实现：`MATCH (s:Symbol)-[r:STEP_IN_PROCESS]->(p:Process {id: $id}) RETURN s.name, r.step ORDER BY r.step`

### `orchard process list --community <id>`

过滤某个社区的 Process。

---

## 实施顺序

1. P1：入口点评分优化（半天，直接影响 process 质量）
2. P3：UX 改进（半天，process show/list 命令）
3. P2：Leiden 算法（1-2 天，需要 pip install + igraph 适配）
