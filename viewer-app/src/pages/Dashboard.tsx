import { useParams, Link } from "react-router";
import { useReport, useCases } from "@/api/client";
import { ERROR_PATTERN_LABELS, type Dimension } from "@/api/client";
import { buildDimensionLabels } from "@/api/useDimensions";
import { ScoreCard } from "@/components/ScoreCard";
import {
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";

/** 雷达图数据项 */
interface RadarDataItem {
  dimension: string;
  score: number;
  fullMark: number;
}

/** 直方图数据项 */
interface HistogramDataItem {
  range: string;
  count: number;
}

/** 将 case 分数转换为直方图分布 */
function buildHistogram(cases: { total_score: number }[]): HistogramDataItem[] {
  const buckets: Record<string, number> = {
    "0-1": 0,
    "1-2": 0,
    "2-3": 0,
    "3-4": 0,
    "4-5": 0,
  };
  for (const c of cases) {
    const score = c.total_score;
    if (score < 1) buckets["0-1"]++;
    else if (score < 2) buckets["1-2"]++;
    else if (score < 3) buckets["2-3"]++;
    else if (score < 4) buckets["3-4"]++;
    else buckets["4-5"]++;
  }
  return Object.entries(buckets).map(([range, count]) => ({ range, count }));
}

/** 将维度分数转换为雷达图数据 */
function buildRadarData(
  dimensionScores: Record<string, number>,
  dimensionLabels: Record<string, string>
): RadarDataItem[] {
  return Object.entries(dimensionScores).map(([key, value]) => ({
    dimension: dimensionLabels[key] ?? key,
    score: value,
    fullMark: 5,
  }));
}

/** Dashboard 页面 - 评估总览 */
export function Dashboard() {
  const { runId } = useParams<{ runId: string }>();
  const { data: report, error: reportError } = useReport(runId ?? "");
  const { data: cases } = useCases(runId ?? "");

  /** 无 runId 时的空状态 */
  if (!runId) {
    return (
      <div className="flex-1 p-6">
        <h1 className="text-2xl font-bold mb-4">Dashboard</h1>
        <p className="text-muted-foreground">选择左侧导航树的节点查看评估数据</p>
      </div>
    );
  }

  /** 加载状态 */
  if (!report && !reportError) {
    return (
      <div className="flex-1 p-6">
        <h1 className="text-2xl font-bold mb-4">Dashboard</h1>
        <p className="text-muted-foreground">加载中...</p>
      </div>
    );
  }

  /** 错误状态 */
  if (reportError || !report) {
    return (
      <div className="flex-1 p-6">
        <h1 className="text-2xl font-bold mb-4">Dashboard</h1>
        <p className="text-destructive">加载报告失败，请检查 run 是否存在</p>
      </div>
    );
  }

  const dimensions: Dimension[] = report.dimensions ?? [];
  const dimensionLabels = buildDimensionLabels(dimensions);
  const radarData = buildRadarData(report.dimension_scores ?? {}, dimensionLabels);
  const histogramData = buildHistogram(cases ?? report.cases_summary ?? []);
  const errorPatternsSummary = report.error_patterns_summary ?? {};

  // 动态改进建议
  const suggestions: string[] = [];
  // 错误模式相关建议
  const errorSuggestions: Record<string, string> = {
    infinite_loop: "检测到无限循环调用。建议强化退出规则：同一参数不得重复调用、3 次空结果后强制停止。",
    null_params: "检测到多次空参数调用。建议确保必填参数始终有值。",
    no_tool_use: "检测到未使用任何工具直接回答。建议强化'所有事实性问题必须使用工具'的约束。",
    wrong_tool_order: "检测到工具调用顺序违反五步管线。建议确保 Entity 在 Corpus 之前。",
    result_ignored: "检测到连续空结果后仍继续搜索。建议使用 ReadChapter 作为兜底工具。",
  };
  for (const pattern of Object.keys(errorPatternsSummary)) {
    if (errorSuggestions[pattern]) {
      suggestions.push(errorSuggestions[pattern]);
    }
  }
  // 低分维度建议
  const dimSuggestions: Record<string, string> = {
    call_efficiency: "调用效率过低。建议优化五步管线的提前退出策略。",
    result_utilization: "结果利用率低。建议确保每步结果都指导下一步。",
    param_quality: "参数质量不足。建议确保 query 为单关键词、rel_type 从概览复制。",
    format_compliance: "格式合规性不足。建议严格遵循模板结构。",
  };
  for (const [dim, score] of Object.entries(report.dimension_scores ?? {})) {
    if (score < 3.0 && dimSuggestions[dim]) {
      suggestions.push(dimSuggestions[dim]);
    }
  }

  return (
    <div className="flex-1 p-6 overflow-auto">
      <h1 className="text-2xl font-bold mb-6">Dashboard</h1>

      {/* 指标卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <ScoreCard
          title="通过率"
          value={`${(report.pass_rate * 100).toFixed(1)}%`}
          subtitle={`${report.total_cases} 个用例`}
        />
        <ScoreCard
          title="总均分"
          value={report.total_score.toFixed(2)}
          subtitle="满分 5.0"
        />
        <ScoreCard
          title="用例数"
          value={report.total_cases}
        />
        <ScoreCard
          title="Run ID"
          value={runId}
          valueClassName="text-base font-mono"
        />
      </div>

      {/* 图表区域 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {/* 雷达图 - 四维评估 */}
        <div className="rounded-lg border bg-card shadow-sm p-4">
          <h3 className="text-sm font-medium mb-3">多维评估</h3>
          <ResponsiveContainer width="100%" height={300}>
            <RadarChart data={radarData}>
              <PolarGrid />
              <PolarAngleAxis
                dataKey="dimension"
                tick={{ fontSize: 12 }}
              />
              <PolarRadiusAxis
                angle={90}
                domain={[0, 5]}
                tick={{ fontSize: 10 }}
              />
              <Radar
                dataKey="score"
                stroke="hsl(220, 70%, 50%)"
                fill="hsl(220, 70%, 50%)"
                fillOpacity={0.3}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        {/* 直方图 - 分数分布 */}
        <div className="rounded-lg border bg-card shadow-sm p-4">
          <h3 className="text-sm font-medium mb-3">分数分布</h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={histogramData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="range" tick={{ fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar
                dataKey="count"
                fill="hsl(220, 70%, 50%)"
                radius={[4, 4, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* 错误模式统计 */}
      {Object.keys(errorPatternsSummary).length > 0 && (
        <div className="rounded-lg border bg-card shadow-sm p-4 mb-6">
          <h3 className="text-sm font-medium mb-3">错误模式统计</h3>
          <div className="space-y-3">
            {Object.entries(errorPatternsSummary).map(([pattern, info]) => {
              const patternInfo = ERROR_PATTERN_LABELS[pattern];
              const isHigh = patternInfo?.color === "red";
              return (
                <div key={pattern} className="flex items-start gap-3">
                  <span className={`inline-block w-2 h-2 rounded-full mt-1.5 shrink-0 ${isHigh ? "bg-red-500" : "bg-yellow-500"}`} />
                  <div>
                    <div className="text-sm font-medium">
                      {patternInfo?.label ?? pattern}
                      <span className="text-muted-foreground font-normal ml-2">
                        {info.count} 个用例受影响
                      </span>
                    </div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {info.affected_cases.map((caseId) => (
                        <Link
                          key={caseId}
                          to={`/case/${encodeURIComponent(runId)}/${encodeURIComponent(caseId)}`}
                          className="text-primary underline hover:no-underline mr-2"
                        >
                          {caseId}
                        </Link>
                      ))}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 动态改进建议 */}
      {suggestions.length > 0 && (
        <div className="rounded-lg border bg-card shadow-sm p-4 mb-6">
          <h3 className="text-sm font-medium mb-3">改进建议</h3>
          <div className="space-y-2">
            {suggestions.map((s, i) => (
              <div key={i} className="flex items-start gap-2">
                <span className="text-yellow-500 text-sm mt-0.5">&#9888;</span>
                <div className="text-sm">{s}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 用例列表 */}
      <div className="rounded-lg border bg-card shadow-sm">
        <div className="p-4 border-b">
          <h3 className="text-sm font-medium">用例列表</h3>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-muted-foreground">
              <th className="text-left p-3">Case ID</th>
              <th className="text-left p-3">问题</th>
              <th className="text-right p-3">得分</th>
              <th className="text-right p-3">耗时(秒)</th>
            </tr>
          </thead>
          <tbody>
            {(report.cases_summary ?? []).map(
              (c: { case_id: string; question: string; total_score: number; execution_time?: number }) => (
                <tr key={c.case_id} className="border-b last:border-b-0 hover:bg-accent/50">
                  <td className="p-3">
                    <a
                      href={`/case/${encodeURIComponent(runId ?? "")}/${encodeURIComponent(c.case_id)}`}
                      className="text-primary underline hover:no-underline"
                    >
                      {c.case_id}
                    </a>
                  </td>
                  <td className="p-3 max-w-xs truncate">{c.question}</td>
                  <td className="p-3 text-right font-mono">{c.total_score.toFixed(2)}</td>
                  <td className="p-3 text-right font-mono">{c.execution_time?.toFixed(1) ?? "-"}</td>
                </tr>
              )
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
