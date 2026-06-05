import { useState, useEffect } from "react";
import { useParams } from "react-router";
import { useRunInfo, useReport, useCase, useCaseMessages, useCaseMessagesJsonl, ERROR_PATTERN_LABELS } from "@/api/client";
import type { Dimension } from "@/api/client";
import { buildDimensionLabels } from "@/api/useDimensions";
import { ToolTimeline } from "@/components/ToolTimeline";
import { MessageChain } from "@/components/MessageChain";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

/** TOC 导航项配置 */
const TOC_ITEMS = [
  { id: "info", label: "基本信息" },
  { id: "tools", label: "工具时间线" },
  { id: "answer", label: "最终答案" },
  { id: "scores", label: "得分明细" },
  { id: "messages", label: "对话记录" },
];

/** 维度中文映射（动态从 report.dimensions 构建） */

/** 得分数据结构 */
interface ScoreData {
  scores: Record<string, number>;
  total_score: number;
  commentary: string;
}

/** Case 详情数据结构 */
interface CaseData {
  case_id: string;
  question: string;
  tool_calls: {
    step: number;
    tool_name: string;
    params: Record<string, unknown>;
    result_summary: string;
  }[];
  final_answer: string;
  execution_time: number;
  error_patterns?: string[];
  score: ScoreData;
}

/** 构建维度得分条形图数据 */
function buildScoreBarData(scores: Record<string, number>, dimensionLabels: Record<string, string>) {
  return Object.entries(scores).map(([key, value]) => ({
    dimension: dimensionLabels[key] ?? key,
    score: value,
  }));
}

/** Case 详情页面 - 单个评估用例的详细分析 */
export function CaseDetail() {
  const { runId, caseId } = useParams<{ runId: string; caseId: string }>();
  const { data: runInfo } = useRunInfo(runId ?? "");
  const { data: report } = useReport(runId ?? "");
  const { data: caseData, error: caseError } = useCase(runId ?? "", caseId ?? "");
  const { data: messagesData } = useCaseMessages(runId ?? "", caseId ?? "");
  const { data: messagesJsonl } = useCaseMessagesJsonl(runId ?? "", caseId ?? "");

  const [copied, setCopied] = useState(false);
  const [activeSection, setActiveSection] = useState("info");

  /** IntersectionObserver 监听当前可视板块 */
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id);
          }
        }
      },
      { threshold: 0.3 }
    );

    TOC_ITEMS.forEach(({ id }) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });

    return () => observer.disconnect();
  }, []);

  /** 无 caseId */
  if (!caseId) {
    return (
      <div className="flex-1 p-6">
        <h1 className="text-2xl font-bold mb-4">Case 详情</h1>
        <p className="text-muted-foreground">未指定 case ID</p>
      </div>
    );
  }

  /** 加载状态 */
  if (!caseData && !caseError) {
    return (
      <div className="flex-1 p-6">
        <h1 className="text-2xl font-bold mb-4">Case 详情</h1>
        <p className="text-muted-foreground">加载中...</p>
      </div>
    );
  }

  /** 错误状态 */
  if (caseError || !caseData) {
    return (
      <div className="flex-1 p-6">
        <h1 className="text-2xl font-bold mb-4">Case 详情</h1>
        <p className="text-destructive">加载 Case 数据失败</p>
      </div>
    );
  }

  const data = caseData as CaseData;
  const dimensions: Dimension[] = report?.dimensions ?? [];
  const dimensionLabels = buildDimensionLabels(dimensions);
  const scoreBarData = buildScoreBarData(data.score?.scores ?? {}, dimensionLabels);

  return (
    <div className="flex-1 p-6 flex gap-6">
      {/* 主内容区 - 可滚动 */}
      <div className="flex-1 min-w-0 overflow-auto">
        <h1 className="text-2xl font-bold mb-6">Case 详情</h1>

      {runInfo && (
        <nav className="text-sm text-muted-foreground mb-4 flex items-center gap-1">
          <a href="/dashboard" className="hover:text-primary">{runInfo.book}</a>
          <span>&gt;</span>
          <a href="/dashboard" className="hover:text-primary">{runInfo.agent}</a>
          <span>&gt;</span>
          <a href={`/dashboard/${encodeURIComponent(runInfo.run_id)}`} className="hover:text-primary">{runInfo.scenario}</a>
          <span>&gt;</span>
          <span className="text-foreground">{caseId}</span>
        </nav>
      )}

      {runInfo && (
        <div className="text-sm text-muted-foreground mb-4 flex items-center gap-4">
          <span>模型: <span className="text-foreground font-medium">{runInfo.model}</span></span>
          <span>时间: <span className="text-foreground font-medium">{runInfo.timestamp.replace("_", " ").replace(/(\d{4}-\d{2}-\d{2}) (\d{2})(\d{2})(\d{2})/, "$1 $2:$3:$4")}</span></span>
        </div>
      )}

      {/* 基本信息卡片 */}
      <div id="info" className="rounded-lg border bg-card shadow-sm p-4 mb-6">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <div className="text-xs text-muted-foreground mb-1">Case ID</div>
            <div className="text-sm font-mono">{data.case_id}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">问题</div>
            <div className="text-sm">{data.question}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">总得分</div>
            <div className="text-lg font-bold">{data.score?.total_score?.toFixed(2) ?? "-"}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">执行耗时</div>
            <div className="text-sm">{data.execution_time?.toFixed(1) ?? "-"}s</div>
          </div>
        </div>
        {/* 错误模式 badge */}
        {data.error_patterns && data.error_patterns.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-3 pt-3 border-t">
            {data.error_patterns.map((pattern) => {
              const info = ERROR_PATTERN_LABELS[pattern];
              const isHigh = info?.color === "red";
              return (
                <span
                  key={pattern}
                  title={info?.label ?? pattern}
                  className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                    isHigh
                      ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                      : "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400"
                  }`}
                >
                  {info?.label ?? pattern}
                </span>
              );
            })}
          </div>
        )}
      </div>

      {/* ToolTimeline 工具调用时间线 */}
      <div id="tools" className="rounded-lg border bg-card shadow-sm p-4 mb-6">
        <h3 className="text-sm font-medium mb-3">工具调用时间线</h3>
        <ToolTimeline toolCalls={data.tool_calls ?? []} />
      </div>

      {/* 最终答案 */}
      {data.final_answer && (
        <div id="answer" className="rounded-lg border bg-card shadow-sm p-4 mb-6">
          <h3 className="text-sm font-medium mb-2">最终答案</h3>
          <div className="text-sm whitespace-pre-wrap">{data.final_answer}</div>
        </div>
      )}

      {/* 得分明细 */}
      <div id="scores" className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {/* 维度得分条形图 */}
        <div className="rounded-lg border bg-card shadow-sm p-4">
          <h3 className="text-sm font-medium mb-3">维度得分</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={scoreBarData} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" domain={[0, 5]} tick={{ fontSize: 12 }} />
              <YAxis
                type="category"
                dataKey="dimension"
                width={80}
                tick={{ fontSize: 12 }}
              />
              <Tooltip />
              <Bar
                dataKey="score"
                fill="hsl(220, 70%, 50%)"
                radius={[0, 4, 4, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Judge 评语 */}
        <div className="rounded-lg border bg-card shadow-sm p-4">
          <h3 className="text-sm font-medium mb-3">Judge 评语</h3>
          <div className="text-sm whitespace-pre-wrap text-muted-foreground">
            {data.score?.commentary ?? "无评语"}
          </div>
        </div>
      </div>

      {/* 对话记录 */}
      <div id="messages" className="rounded-lg border bg-card shadow-sm p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium">对话记录</h3>
          {messagesData?.content && (
            <button
              onClick={() => {
                navigator.clipboard.writeText(messagesData.content);
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
              }}
              className="text-xs px-2 py-1 rounded border hover:bg-accent transition-colors"
            >
              {copied ? "已复制 ✓" : "复制"}
            </button>
          )}
        </div>
        {messagesJsonl && messagesJsonl.length > 0 ? (
          <MessageChain messages={messagesJsonl} />
        ) : messagesData?.content ? (
          <pre className="text-xs whitespace-pre-wrap bg-muted/50 p-4 rounded-lg overflow-auto max-h-[600px] font-mono leading-relaxed">
            {messagesData.content}
          </pre>
        ) : (
          <div className="text-sm text-muted-foreground">无对话记录</div>
        )}
      </div>
      </div>

      {/* 悬浮 TOC - 仅 1440px 以上显示，始终可见 */}
      <div className="toc-panel w-36 shrink-0 self-start sticky top-6">
        <nav>
          <div className="text-xs font-medium text-muted-foreground mb-3">目录</div>
          <div className="space-y-1">
            {TOC_ITEMS.map(({ id, label }) => (
              <a
                key={id}
                href={`#${id}`}
                onClick={(e) => {
                  e.preventDefault();
                  document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
                }}
                className={`block text-sm py-1 px-2 rounded transition-colors ${
                  activeSection === id
                    ? "text-primary font-medium bg-primary/5"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {label}
              </a>
            ))}
          </div>
        </nav>
      </div>
    </div>
  );
}
