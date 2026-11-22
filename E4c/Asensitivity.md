**What to check before we start**
- 1 km 基准结果
- 1 km尺度 的 patm 算法（check parameters, input and output）& 
- 500m 尺度的数据有没有（之前有没有算）
- top-k 选的什么指标的 top-k？
> B 在 11/18 提供可切换空间尺度的“方向事件构造 → 潮汐规则统计 → 排名”流程，以及运行说明；

**问题**
| 编号 | 实验内容 | 要回答的问题 |
|---|---|---|
| 实验1 | 1 km 潮汐结果 vs 500 m 潮汐结果 | 换成更细的区域划分后，发现的潮汐模式发生什么变化？ |
实验2 | 按日期、时窗和方向，把 500 m 车次汇总到 1 km 父格，排除起终点落在同一父格的骑行，再检查是否与已有 1 km OD 数据一致。|1km 尺度的计算是否正确

**详细的实验方法**
1. **取得 1 km 基准结果。**  
   使用全年工作日 AM/PM、已冻结的参数和覆盖规则。如果 B 已交付相同口径的结果，直接复用，不必重复计算。  

2. **运行一遍原生 500 m 潮汐分析。**  
- 500 m 尺度说明 + 数据获取
    假设 1 km 区域 P、Q 各自包含四个 500 m 子格：
    - **1 km 分析**：研究 P ↔ Q 这一条走廊。
    - **500 m 分析**：分别研究 P₁ ↔ Q₁、P₁ ↔ Q₂、P₂ ↔ Q₁……这些细尺度走廊。
- 分析过程 （使用的参数）
    - 使用相同日期口径、`n_min`、`p_min`、`minsup` 和排名规则；候选筛选规则相同
    - 区域 ID 和候选走廊使用500m尺度下的。
- 分析结果
    - 500 m 尺度下的潮汐 Top-k 走廊

3. **500m 尺度父格映射与 1km 尺度结果对比**  
把 500 m 潮汐 Top-k 的两个端点映射到 1 km 父格
遍历 500 m 潮汐 Top-k，区分：

| 编号 | 表格名称 | 内容 | 表格字段 |
|---|---|---|---|
| 1 | `1.csv` | 比较 500 m AM 方向映射到父格后的方向与对应 1 km 潮汐走廊的 AM 方向 | Top-k 排名, `corridor_500m`, `parent_corridor_1km`, `direction_500m_AM`, `direction_500_to_1km`, `direction_1km_AM`, `direction_match` (yes/no) |
| 2 | `2.csv` | 同一父格走廊中的多条细走廊，是否存在方向差异 | Top-k 排名,`parent_corridor_1km`, `direction_1km`, `corridor_500m`, `direction_500m_AM`, `direction_500_to_1km`,`different_directions?` (yes/no. 表示同意父格中的走廊时候存在方向差异，这个表按照父格合并就父格那一栏比较大统一记录信息，500m子格里面也记录小个的信息) |
| 3 | `3.csv` | 两端落在同一父格——这是细尺度新增可见的内部流动 |Top-k 排名, `parent_grid`, `corridor_500m`, `direction_500m_AM` |
| 4 | `4.csv` | 对应的父格走廊没有进入 1 km Top-k | Top-k 排名,`corridor_500m`, `parent_corridor_1km`, `1km_topk_status` |

注意，在表格中这样表示无向走廊：A-B (走廊起点终点格，alphabetic order 小的放在前面)；这样表示方向：A -> B

4. **做一次父格车次对账**  
   - 检查空间聚合是否对得上。
把 500 m 车次映射到父格，汇总跨父格流量，排除父格内部骑行，再和 1 km 的同日期、时段、方向 OD 计数比较。如果对不上，就输出一个summary对不上的记录的文件。
   - 满足阈值的规则数：1km 尺度的和 500m 尺度的统计一下，写到新开的`statisitics.json`里面

**代码输出**

| 文件名称 | 内容 |
|---|---|
| `500m_direction_events.parquet` | 保存 500 m 全部候选走廊按日期和 AM/PM 计算的双向车次、总车次、主方向与覆盖状态，供复查和后续分析。 |
| `500m_tidal_rules.csv` | 保存 500 m 尺度满足潮汐条件的走廊及其代表朝向、支持天数、可观察日数、足量日数、support 和 confidence。 |
| `500m_tidal_topk.csv` | 按预先确定的潮汐排序指标输出 500 m Top-k 走廊、排名、方向和相关指标。 |
| `1.csv` | 比较 500 m AM 方向映射到父格后的方向与对应 1 km 潮汐走廊的 AM 方向。字段：Top-k 排名、`corridor_500m`、`parent_corridor_1km`、`direction_500m_AM`、`direction_500_to_1km`、`direction_1km_AM`、`direction_match` (yes/no)。 |
| `2.csv` | 列出映射到同一父格走廊的细走廊及其方向差异。字段：`parent_corridor_1km`、`direction_1km`、`corridor_500m`、`direction_500m_AM`、`different_directions?` (yes/no)。 |
| `3.csv` | 列出两端落在同一父格、仅在 500 m 尺度可见的内部流动。字段：`parent_grid`、`corridor_500m`、`direction_500m_AM`。 |
| `4.csv` | 列出对应父格走廊没有进入 1 km Top-k 的 500 m Top-k 走廊。字段：`corridor_500m`、`parent_corridor_1km`、`1km_topk_status`。 |
| `check.csv` | 按日期、时段和方向汇总 500 m 跨父格车次，与 1 km OD 车次逐项比较之后的，记录两者计数、差值和不一致的所有记录（排除起终点落在同一父格的骑行） |
| `statistics.json` | 汇总 1 km 与 500 m 的候选走廊数、满足阈值的规则数、Top-k 重合及方向比较统计，并记录父格对账的匹配和不匹配数量。 |

**代码文件细明**

| `.py 脚本` | 内容 |
|---|---|
| `check.py` | 读取两尺度 OD 和 `regions.parquet`，按 `parent_region` 映射并汇总 500 m 跨父格车次，排除同父格内部流动，与 1 km 对应车次核对，输出 `check.csv`。 |
| `mine_500m_patterns.py` | 读取 500 m OD，沿用 1 km 分析的日期与覆盖口径、冻结参数和方向规则，计算并保存 500 m 方向事件、潮汐规则及 Top-k 结果；不执行跨尺度 Top-k 比较。 |
| `compare_topk.py` | 读取已保存的 500 m pattern mining 结果、1 km 潮汐结果和区域父格映射，生成 `1.csv` 至 `4.csv` 及 `statistics.json`；不重新运行 500 m pattern mining。 |

// comparison logic (no need to read when writing code)
应分别统计这些现象：
- 500 m 潮汐走廊映射后，1 km 没有潮汐结果；
- 500 m 与 1 km 的主方向不一致；
- 多条 500 m 子走廊映射到同一父格走廊后，方向相互抵消；
- 500 m 两端在同一 1 km 父格内，因此 1 km 跨区域表看不到；
- 两个尺度的 Top-k 走廊重合率、方向一致率和排名变化。

然后这样解释：
- 差异少、核心走廊和方向大多保持：结果对空间尺度较稳健。
- 差异多，但能由方向抵消、父格内流动或细格稀疏解释：说明空间尺度敏感，粗尺度会掩盖局部结构；
//
- 差异无法由这些机制解释，或父格车次对账不一致：应先检查计算、映射或输入数据。
- 500 m 只有少量车次导致大量不可判定：反映细尺度数据稀疏。
所以，结论不能只看“掩盖数量”，还要结合掩盖比例、方向一致率、Top-k 重合率、支持天数和父格对账结果。
