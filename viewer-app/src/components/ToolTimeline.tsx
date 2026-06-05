import { useMemo, useState } from "react";
import { computeDuplicateFlags } from "./ToolTimeline.utils";

/** 工具调用时间线组件 - 展示 Case 中工具调用的时序 */

/** 单次工具调用的数据结构 */
interface ToolCall {
  step: number;
  tool_name: string;
  params: Record<string, unknown>;
  result_summary: string;
}

interface ToolTimelineProps {
  /** 工具调用列表 */
  toolCalls: ToolCall[];
}

/** 将参数对象格式化为简短字符串（折叠态） */
function formatParamsTruncated(params: Record<string, unknown>): string {
  const entries = Object.entries(params);
  if (entries.length === 0) return "(无参数)";
  return entries
    .slice(0, 3)
    .map(([k, v]) => {
      const val = typeof v === "string" ? v : JSON.stringify(v);
      const truncated = val.length > 40 ? val.slice(0, 40) + "..." : val;
      return `${k}: ${truncated}`;
    })
    .join(", ");
}

/** 判断参数是否需要展开按钮（超过 3 个参数，或任意值超过 40 字符） */
function shouldShowParamsExpand(params: Record<string, unknown>): boolean {
  const entries = Object.entries(params);
  if (entries.length > 3) return true;
  return entries.some(([, v]) => {
    const val = typeof v === "string" ? v : JSON.stringify(v);
    return val.length > 40;
  });
}

/** 可展开文本行组件 */
function ExpandableSection({
  label,
  text,
  maxLength,
  asJson,
}: {
  label: string;
  text: string;
  maxLength: number;
  asJson?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const needsExpand = text.length > maxLength;

  if (!needsExpand) {
    return (
      <div className="text-xs text-muted-foreground">
        <span className="font-medium">{label}: </span>
        {text}
      </div>
    );
  }

  if (!expanded) {
    return (
      <div className="text-xs text-muted-foreground">
        <span className="font-medium">{label}: </span>
        {text.slice(0, maxLength) + "..."}
        <button
          onClick={() => setExpanded(true)}
          className="ml-1 text-primary hover:underline cursor-pointer"
        >
          展开 ▼
        </button>
      </div>
    );
  }

  /** 展开态 */
  const content = asJson
    ? JSON.stringify(JSON.parse(text), null, 2)
    : text;

  return (
    <div className="text-xs text-muted-foreground">
      <span className="font-medium">{label}: </span>
      <pre className="mt-1 p-2 bg-muted/50 rounded text-xs whitespace-pre-wrap overflow-auto max-h-[300px] font-mono leading-relaxed">
        {content}
      </pre>
      <button
        onClick={() => setExpanded(false)}
        className="text-primary hover:underline cursor-pointer"
      >
        收起 ▲
      </button>
    </div>
  );
}

export function ToolTimeline({ toolCalls }: ToolTimelineProps) {
  if (!toolCalls || toolCalls.length === 0) {
    return (
      <div className="flex items-center justify-center h-24 bg-muted/50 rounded-lg">
        <span className="text-sm text-muted-foreground">无工具调用记录</span>
      </div>
    );
  }

  /** 全局重复标记（与后端 report.py 对齐） */
  const duplicateFlags = useMemo(
    () => computeDuplicateFlags(toolCalls),
    [toolCalls]
  );
  const isRepeated = (idx: number): boolean => duplicateFlags[idx];

  return (
    <div className="space-y-0">
      {toolCalls.map((call, idx) => {
        const repeated = isRepeated(idx);
        return (
          <ToolCallCard key={idx} call={call} idx={idx} repeated={repeated} total={toolCalls.length} />
        );
      })}
    </div>
  );
}

/** 单个工具调用卡片 */
function ToolCallCard({ call, idx, repeated, total }: {
  call: ToolCall;
  idx: number;
  repeated: boolean;
  total: number;
}) {
  const [paramsExpanded, setParamsExpanded] = useState(false);
  const showParamsExpand = shouldShowParamsExpand(call.params);

  return (
    <div className="relative flex gap-3">
      {/* 时间线连接线 */}
      <div className="flex flex-col items-center">
        {/* 步骤编号圆点 */}
        <div
          className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${
            repeated
              ? "bg-red-100 text-red-700 border-2 border-red-400"
              : "bg-primary/10 text-primary border-2 border-primary/30"
          }`}
        >
          {call.step}
        </div>
        {/* 连接线 */}
        {idx < total - 1 && (
          <div className="w-0.5 flex-1 bg-border min-h-4" />
        )}
      </div>

      {/* 调用详情卡片 */}
      <div
        className={`flex-1 mb-3 p-3 rounded-lg border ${
          repeated
            ? "border-red-300 bg-red-50"
            : "border-border bg-card"
        }`}
      >
        <div className="flex items-center gap-2 mb-1">
          <span className={`text-sm font-mono font-semibold ${repeated ? "text-red-700" : "text-foreground"}`}>
            {call.tool_name}
          </span>
          {repeated && (
            <span className="text-xs bg-red-200 text-red-800 px-1.5 py-0.5 rounded">
              重复调用
            </span>
          )}
        </div>

        {/* 参数区域 */}
        {paramsExpanded ? (
          <div className="text-xs text-muted-foreground mb-1">
            <span className="font-medium">参数: </span>
            <pre className="mt-1 p-2 bg-muted/50 rounded text-xs whitespace-pre-wrap overflow-auto max-h-[300px] font-mono leading-relaxed">
              {JSON.stringify(call.params, null, 2)}
            </pre>
            <button
              onClick={() => setParamsExpanded(false)}
              className="text-primary hover:underline cursor-pointer"
            >
              收起 ▲
            </button>
          </div>
        ) : (
          <div className="text-xs text-muted-foreground mb-1">
            {formatParamsTruncated(call.params)}
            {showParamsExpand && (
              <button
                onClick={() => setParamsExpanded(true)}
                className="ml-1 text-primary hover:underline cursor-pointer"
              >
                展开 ▼
              </button>
            )}
          </div>
        )}

        {/* 结果区域 */}
        {call.result_summary && (
          <ExpandableSection
            label="结果"
            text={call.result_summary}
            maxLength={120}
          />
        )}
      </div>
    </div>
  );
}
