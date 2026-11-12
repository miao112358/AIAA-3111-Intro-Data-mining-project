# 2025 NYC Citi Bike 数据预处理交付说明
重要的处理见 Akey_decision.md
## 1. 文件
### 1.1 核心分析与覆盖文件

| 文件 | 内容及用途 |
| --- | --- |
| `od_windows.parquet` | **待分析的数据全部在这里** 不同区域之间、AM/MID/PM 三个窗口的正骑行数汇总。是 pattern mining 的主要流量输入，尚未转换成交易或规则。 |
| `date_coverage.csv` | 365 天的日期、日型、开发/留出期、导入状态、各窗口覆盖状态和观察车次。用于构造日期全集和确定分母。 | 
| `regions.parquet` | **有用！** 网格 ID、空间尺度、父网格、中心坐标与边界。用于解释区域、画图和跨尺度映射。 |
| `coverage_review_template.csv` | 按日期和 AM/MID/PM 列出的待复核表，用 `status` 和 `reason` 记录覆盖状态及依据。 |

### 1.2 数据质量文件
| 文件 | 内容及用途 |
| --- | --- |
| `quality_monthly.csv` | 按来源月份和最终处理原因统计行数。每条原始记录只计入一个最终结果，`valid + 各类排除 = 原始记录数`。 |
| `quality_flags.csv` | 缺站名、未知用户类别等辅助标记统计。统计对象是去重后进入清洗的候选记录，可能包括之后被排除的记录；不同标记可以重叠，不能相加当作排除数。 |
| `excluded_records.parquet` | 每条被排除记录的来源位置、骑行 ID 和最终排除原因；不含完整原始 13 列。 |

## 2. OD 表与字段

OD 即起点—终点。本项目按日期、出发时窗和有向网格对汇总，三张流量表字段相同：

| 字段 | 含义 |
| --- | --- |
| `local_date` | 纽约本地出发日期，`YYYY-MM-DD`。 |
| `day_type` | `weekday` 为周一至周五，`weekend` 为周六、周日。 |
| `split` | `development` 为 1—9 月，`holdout` 为 10—12 月。 |
| `window` | AM、MID、PM，或补充表中的 OTHER。 |
| `grid_version` | 网格版本，本次为 `utm18n_o0_v1`。 |
| `grid_size_m` | 网格边长，500 或 1000 米。 |
| `origin_region` / `destination_region` | 起区 / 终区 ID。 |
| `ride_count` | 该组合的有效骑行数。 |

唯一键为网格版本、尺度、日期、时窗、起区和终区。**车次应对 `ride_count` 求和，不能数行。** `od_windows` 有 18,798,964 行，含两个尺度。

每条骑行分别进入 500 m 和 1000 m 聚合，**两个尺度不得相加**。任选一个尺度，三张流量表的车次总和为 45,618,904；`od_windows` 不含同区域及窗口外骑行。表中只保存正计数；缺行只有在相应日期、窗口及区域已确认可观察时，才能解释为零车次。

## 3. 空间、时间与清洗口径 (data preprocesing 做的部分事情，GPT写的，感兴趣就随便看看)

- 空间：WGS84 经纬度投影到 UTM 18N（`EPSG:32618`），原点 `(0, 0)`，网格边界左闭右开。1 km 为主尺度；500 m 用于对照，其 `parent_region` 指向 1 km 父格。
- 区域坐标：`center_lon/center_lat` 为中心经纬度；`min_x/min_y/max_x/max_y` 为米制边界；`ix/iy` 为网格索引。
- 时间：按 `America/New_York` 的出发时间归窗，AM `[07:00,10:00)`、MID `[11:00,14:00)`、PM `[16:00,19:00)`，其余为 OTHER。
- 划分：1—9 月开发，10—12 月留出验证。工作日按星期划分；本次未单独标记节假日，全部`is_holiday=False`

全年按字符串 `ride_id` 去重：空 ID 排除；同 ID、规范化后 13 列相同仅保留首条；内容冲突则该 ID 全部隔离。再检查时间、年份和坐标。

起终点任一坐标缺失、非数字、非有限值、超出合法经纬度范围或为 0，即视为无效端点；**未用站点 ID 或站名补全坐标**。缺站名但坐标有效可纳入。

| 本次排除原因 | 条数 |
| --- | ---: |
| `invalid_endpoint`：端点坐标无效 | 147,122 |
| `ambiguous_time`：本地时间歧义 | 4,829 |
| `outside_year`：出发日期不在 2025 年 | 207 |
| `outside_coordinate_envelope`：端点超出包络 | 40 |
| 合计 | 152,198 |

每条记录只计一个最终原因，不重复累计。`excluded_records` 保存来源月份、压缩包、CSV 分片、原行号、骑行 ID、原因及序号；`source_row` 以表头为第 1 行。`source_month` 是来源文件月份，可能与实际出发月份不同。

## 4. 读取示例

用 `pandas` 和 `pyarrow` 在项目根目录读取单日、单尺度数据：

```python
from pathlib import Path
import pandas as pd

p = Path("data/processed/20261111T024449780084Z_8696edd7")
od = pd.read_parquet(
    p / "od_windows.parquet",
    filters=[("grid_size_m", "==", 1000), ("local_date", "==", "2025-02-03")],
    engine="pyarrow",
)
print(od.head())
print("该日跨区域、三窗口车次：", int(od.ride_count.sum()))
coverage = pd.read_csv(p / "date_coverage.csv")
regions = pd.read_parquet(p / "regions.parquet")
```

制图时先选同一尺度，再用 `grid_version` 和区域 ID 将起终点分别关联到 `regions`。
