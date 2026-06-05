import { useState } from "react";
import { useBooks, useAgents, useScenarios, useTrend, useReport } from "@/api/client";
import type { Dimension } from "@/api/client";
import { buildDimensionLabels } from "@/api/useDimensions";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceDot,
} from "recharts";

/** 维度中文映射（动态从 report.dimensions 构建） */

/** 趋势数据中的单条记录 */
interface TrendPoint {
  run_id: string;
  timestamp: string;
  total_score: number;
  dimension_scores: Record<string, number>;
  /** 是否为回归点 */
  is_regression?: boolean;
}

/** 维度对应颜色（按序分配） */
const DIMENSION_COLOR_PALETTE = [
  "hsl(160, 60%, 45%)",
  "hsl(30, 80%, 55%)",
  "hsl(280, 60%, 55%)",
  "hsl(0, 70%, 50%)",
  "hsl(200, 70%, 45%)",
  "hsl(60, 60%, 45%)",
];

/** 总分颜色 */
const TOTAL_SCORE_COLOR = "hsl(220, 70%, 50%)";

/** 格式化时间戳为短日期 */
function formatTime(ts: string): string {
  try {
    const d = new Date(ts);
    return `${(d.getMonth() + 1).toString().padStart(2, "0")}-${d.getDate().toString().padStart(2, "0")} ${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
  } catch {
    return ts;
  }
}

/** 时间趋势页面 - 评估指标随时间变化的趋势图 */
export function Trend() {
  const [selectedBook, setSelectedBook] = useState("");
  const [selectedAgent, setSelectedAgent] = useState("");
  const [selectedScenario, setSelectedScenario] = useState("");
  /** 当前查看的维度，"total" 表示总分，其余为维度 key */
  const [activeDimension, setActiveDimension] = useState("total");

  /** 数据加载 */
  const { data: books } = useBooks();
  const { data: agents } = useAgents(selectedBook);
  const { data: scenarios } = useScenarios(selectedBook, selectedAgent);
  const { data: trendData } = useTrend(selectedBook, selectedAgent, selectedScenario);

  /** 从最新趋势点获取 report 以提取 dimensions 配置 */
  const latestRunId = trendData && trendData.length > 0 ? trendData[trendData.length - 1].run_id : "";
  const { data: latestReport } = useReport(latestRunId);

  /** 动态维度配置 */
  const dimensions: Dimension[] = latestReport?.dimensions ?? [];
  const dimensionLabels = buildDimensionLabels(dimensions);
  const dimensionKeys = dimensions.length > 0
    ? dimensions.map(d => d.key)
    : (trendData && trendData.length > 0 ? Object.keys(trendData[0].dimension_scores ?? {}) : []);

  /** 维度颜色映射 */
  const getDimensionColor = (dimKey: string, index: number): string => {
    if (dimKey === "total_score" || dimKey === "total") return TOTAL_SCORE_COLOR;
    return DIMENSION_COLOR_PALETTE[index % DIMENSION_COLOR_PALETTE.length];
  };

  /** 构建折线图数据 */
  const buildChartData = () => {
    if (!trendData || trendData.length === 0) return [];

    return trendData.map((point: TrendPoint) => {
      return {
        time: formatTime(point.timestamp),
        run_id: point.run_id,
        total: point.total_score,
        ...point.dimension_scores,
        isRegression: point.is_regression ?? false,
      };
    });
  };

  const chartData = buildChartData();

  /** 根据当前维度决定数据 key */
  const getDataKey = (): string => {
    if (activeDimension === "total") return "total";
    return activeDimension;
  };

  /** 获取所有需要显示的线（可以同时显示总分+各维度，或单个维度） */
  const lines = activeDimension === "total"
    ? [{ key: "total", label: "总分", color: TOTAL_SCORE_COLOR }]
    : [{ key: activeDimension, label: dimensionLabels[activeDimension] ?? activeDimension, color: getDimensionColor(activeDimension, dimensionKeys.indexOf(activeDimension)) }];

  return (
    <div className="flex-1 p-6 overflow-auto">
      <h1 className="text-2xl font-bold mb-6">时间趋势</h1>

      {/* 选择器区域 */}
      <div className="flex flex-wrap gap-4 mb-6">
        {/* 书籍选择 */}
        <div>
          <label className="block text-xs text-muted-foreground mb-1">书籍</label>
          <select
            className="border rounded px-3 py-1.5 text-sm bg-card"
            value={selectedBook}
            onChange={(e) => {
              setSelectedBook(e.target.value);
              setSelectedAgent("");
              setSelectedScenario("");
            }}
          >
            <option value="">选择书籍</option>
            {books?.map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>
        </div>

        {/* Agent 选择 */}
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Agent</label>
          <select
            className="border rounded px-3 py-1.5 text-sm bg-card"
            value={selectedAgent}
            onChange={(e) => {
              setSelectedAgent(e.target.value);
              setSelectedScenario("");
            }}
            disabled={!selectedBook}
          >
            <option value="">选择 Agent</option>
            {agents?.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        </div>

        {/* Scenario 选择 */}
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Scenario</label>
          <select
            className="border rounded px-3 py-1.5 text-sm bg-card"
            value={selectedScenario}
            onChange={(e) => setSelectedScenario(e.target.value)}
            disabled={!selectedAgent}
          >
            <option value="">选择 Scenario</option>
            {scenarios?.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
      </div>

      {/* 维度切换器 */}
      <div className="flex flex-wrap gap-2 mb-4">
        <button
          className={`px-3 py-1 text-sm rounded border transition-colors ${
            activeDimension === "total"
              ? "bg-accent border-primary font-medium"
              : "bg-card hover:bg-accent/50"
          }`}
          onClick={() => setActiveDimension("total")}
        >
          总分
        </button>
        {dimensionKeys.map((dim, idx) => (
          <button
            key={dim}
            className={`px-3 py-1 text-sm rounded border transition-colors ${
              activeDimension === dim
                ? "bg-accent border-primary font-medium"
                : "bg-card hover:bg-accent/50"
            }`}
            onClick={() => setActiveDimension(dim)}
          >
            {dimensionLabels[dim] ?? dim}
          </button>
        ))}
      </div>

      {/* 折线图 */}
      {!trendData && (
        <div className="text-muted-foreground text-sm">
          请选择书籍、Agent 和 Scenario 查看趋势
        </div>
      )}

      {chartData.length > 0 && (
        <div className="rounded-lg border bg-card shadow-sm p-4">
          <h3 className="text-sm font-medium mb-3">趋势图</h3>
          <ResponsiveContainer width="100%" height={400}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="time"
                tick={{ fontSize: 11 }}
                angle={-30}
                textAnchor="end"
                height={60}
              />
              <YAxis domain={[0, 5]} tick={{ fontSize: 12 }} />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const data = payload[0].payload;
                  return (
                    <div className="bg-card border rounded p-2 text-xs shadow-sm">
                      <div className="font-medium mb-1">{data.run_id}</div>
                      <div>时间: {data.time}</div>
                      <div>
                        {activeDimension === "total" ? "总分" : (dimensionLabels[activeDimension] ?? activeDimension)}:{" "}
                        {data[getDataKey()]?.toFixed(2)}
                      </div>
                      {data.isRegression && (
                        <div className="text-red-500 font-medium mt-1">检测到回归</div>
                      )}
                    </div>
                  );
                }}
              />
              <Legend />
              {lines.map((line) => (
                <Line
                  key={line.key}
                  type="monotone"
                  dataKey={line.key}
                  name={line.label}
                  stroke={line.color}
                  strokeWidth={2}
                  dot={(props: Record<string, unknown>) => {
                    const { cx, cy, payload } = props as {
                      cx: number;
                      cy: number;
                      payload: { isRegression: boolean };
                    };
                    if (payload.isRegression) {
                      return (
                        <ReferenceDot
                          key={`dot-${cx}-${cy}`}
                          x={cx}
                          y={cy}
                          r={6}
                          fill="hsl(0, 70%, 50%)"
                          stroke="hsl(0, 70%, 50%)"
                        />
                      );
                    }
                    return (
                      <circle
                        key={`dot-${cx}-${cy}`}
                        cx={cx}
                        cy={cy}
                        r={3}
                        fill={line.color}
                        stroke={line.color}
                      />
                    );
                  }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>

          {/* 回归点说明 */}
          <div className="flex items-center gap-4 mt-2 text-xs text-muted-foreground">
            <div className="flex items-center gap-1">
              <span className="inline-block w-3 h-3 rounded-full bg-red-500" />
              回归点（总分下降超过 0.5）
            </div>
          </div>
        </div>
      )}

      {/* 趋势数据表格 */}
      {chartData.length > 0 && (
        <div className="rounded-lg border bg-card shadow-sm mt-6">
          <div className="p-4 border-b">
            <h3 className="text-sm font-medium">历史记录</h3>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-muted-foreground">
                <th className="text-left p-3">Run ID</th>
                <th className="text-left p-3">时间</th>
                <th className="text-right p-3">总分</th>
                {dimensionKeys.map((dim) => (
                  <th key={dim} className="text-right p-3">{dimensionLabels[dim] ?? dim}</th>
                ))}
                <th className="text-center p-3">状态</th>
              </tr>
            </thead>
            <tbody>
              {chartData.map((row, idx) => (
                <tr key={idx} className="border-b last:border-b-0 hover:bg-accent/50">
                  <td className="p-3">
                    <a
                      href={`/dashboard/${row.run_id}`}
                      className="text-primary underline hover:no-underline"
                    >
                      {row.run_id}
                    </a>
                  </td>
                  <td className="p-3">{row.time}</td>
                  <td className="p-3 text-right font-mono">{row.total?.toFixed(2)}</td>
                  {dimensionKeys.map((dim) => (
                    <td key={dim} className="p-3 text-right font-mono">
                      {(row as Record<string, unknown>)[dim] !== undefined
                        ? ((row as Record<string, unknown>)[dim] as number).toFixed(2)
                        : "-"}
                    </td>
                  ))}
                  <td className="p-3 text-center">
                    {row.isRegression ? (
                      <span className="text-red-500 font-medium">回归</span>
                    ) : (
                      <span className="text-green-600">正常</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
