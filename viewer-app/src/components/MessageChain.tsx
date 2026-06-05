/**
 * MessageChain 组件 - 基于 JSONL 的结构化对话记录渲染
 *
 * 每条消息用 Collapsible 卡片包裹：
 * - 折叠态：显示角色图标 + 摘要文本
 * - 展开态：显示完整内容（思考、文本、工具调用、工具结果）
 * - 重复工具调用标红色 badge
 */

import { useMemo, useState } from "react";
import type { MessageItem, ContentBlock, ToolCallInfo } from "@/api/client";
import { computeDuplicateFlags } from "./ToolTimeline.utils";

/** 角色颜色配置 */
const ROLE_CONFIG: Record<string, { label: string; color: string; bgColor: string; borderColor: string }> = {
  system: { label: "System", color: "text-blue-600 dark:text-blue-400", bgColor: "bg-blue-50 dark:bg-blue-950/30", borderColor: "border-l-blue-400" },
  user: { label: "User", color: "text-green-600 dark:text-green-400", bgColor: "bg-green-50 dark:bg-green-950/30", borderColor: "border-l-green-400" },
  assistant: { label: "Assistant", color: "text-purple-600 dark:text-purple-400", bgColor: "bg-purple-50 dark:bg-purple-950/30", borderColor: "border-l-purple-400" },
  tool: { label: "Tool", color: "text-orange-600 dark:text-orange-400", bgColor: "bg-orange-50 dark:bg-orange-950/30", borderColor: "border-l-orange-400" },
};

/** 工具图标映射 */
const TOOL_ICONS: Record<string, string> = {
  SearchEntity: "🔍",
  SearchGraph: "🔗",
  SearchCorpus: "📄",
  ReadChapter: "📖",
  WriteFile: "✏️",
  ReadFile: "📋",
  StrReplaceFile: "🔧",
  Glob: "📂",
  Grep: "🔎",
};

/** 截断文本 */
function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + "...";
}

/** 从 content 中提取纯文本 */
function extractText(content: string | ContentBlock[]): string {
  if (typeof content === "string") return content;
  return content
    .filter((b) => b.type === "text" && b.text)
    .map((b) => b.text!)
    .join("\n");
}

/** 从 content 中提取思考内容 */
function extractThink(content: string | ContentBlock[]): string | null {
  if (typeof content === "string") return null;
  const thinkBlock = content.find((b) => b.type === "think" && b.think);
  return thinkBlock?.think ?? null;
}

/** 构建折叠态摘要 */
function buildSummary(msg: MessageItem, idx: number): string {
  const { role, content } = msg;

  if (role === "system") {
    const text = typeof content === "string" ? content : "";
    return `System Prompt · ${text.length} 字`;
  }

  if (role === "user") {
    const text = extractText(content);
    // 过滤 system-reminder
    if (text.includes("(system-reminder)")) return "(system-reminder)";
    return truncate(text, 80);
  }

  if (role === "assistant") {
    const text = extractText(content);
    const think = extractThink(content);
    const toolCount = msg.tool_calls?.length ?? 0;
    const parts: string[] = [];
    if (think) parts.push("思考中...");
    if (text) parts.push(truncate(text, 60));
    if (toolCount > 0) parts.push(`${toolCount} 个工具调用`);
    return parts.length > 0 ? parts.join(" · ") : "（无内容）";
  }

  if (role === "tool") {
    const text = extractText(content);
    // 尝试从内容中提取关键信息
    const cleanText = text
      .replace(/<system>.*?<\/system>/g, "")
      .replace(/<system-reminder>.*?<\/system-reminder>/g, "")
      .trim();
    return cleanText ? truncate(cleanText, 80) : truncate(text, 80);
  }

  return "";
}

/** 构建 assistant 消息的编号 */
function useAssistantNumbers(messages: MessageItem[]): Map<number, number> {
  return useMemo(() => {
    const map = new Map<number, number>();
    let count = 0;
    messages.forEach((msg, idx) => {
      if (msg.role === "assistant") {
        count++;
        map.set(idx, count);
      }
    });
    return map;
  }, [messages]);
}

/** 构建全局工具调用索引（用于重复检测） */
function buildGlobalToolCallIndex(messages: MessageItem[]): {
  toolCalls: { tool_name: string; params: Record<string, unknown> }[];
  /** messageIdx -> toolCallIdx 在全局数组中的起始位置 */
  messageToolCallRanges: Map<number, [number, number]>;
} {
  const toolCalls: { tool_name: string; params: Record<string, unknown> }[] = [];
  const messageToolCallRanges = new Map<number, [number, number]>();

  messages.forEach((msg, idx) => {
    if (msg.role === "assistant" && msg.tool_calls) {
      const start = toolCalls.length;
      for (const tc of msg.tool_calls) {
        let params: Record<string, unknown> = {};
        try {
          params = JSON.parse(tc.function.arguments);
        } catch { /* ignore */ }
        toolCalls.push({ tool_name: tc.function.name, params });
      }
      messageToolCallRanges.set(idx, [start, toolCalls.length]);
    }
  });

  return { toolCalls, messageToolCallRanges };
}

// ============================================================
// 子组件
// ============================================================

/** 思考内容块 */
function ThinkBlock({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="mb-2">
      <button
        onClick={() => setExpanded(!expanded)}
        className="text-xs text-muted-foreground hover:text-foreground cursor-pointer flex items-center gap-1"
      >
        <span className="text-purple-500">💭</span>
        <span className="font-medium">思考</span>
        <span className="text-[10px]">{expanded ? "▲" : "▼"}</span>
      </button>
      {expanded && (
        <div className="mt-1 ml-4 text-xs text-muted-foreground bg-muted/30 p-2 rounded whitespace-pre-wrap max-h-[200px] overflow-auto">
          {text}
        </div>
      )}
    </div>
  );
}

/** 单个工具调用项 */
function ToolCallItem({
  tc,
  repeated,
}: {
  tc: ToolCallInfo;
  repeated: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  let params: Record<string, unknown> = {};
  try {
    params = JSON.parse(tc.function.arguments);
  } catch { /* ignore */ }

  const displayName = tc.function.name;
  const icon = TOOL_ICONS[displayName] ?? "🔧";

  // 参数摘要
  const paramEntries = Object.entries(params);
  const shortSummary = paramEntries
    .slice(0, 2)
    .map(([k, v]) => {
      const val = typeof v === "string" ? v : JSON.stringify(v);
      return `${k}: ${truncate(val, 30)}`;
    })
    .join(", ");

  return (
    <div
      className={`rounded border p-2 ${
        repeated
          ? "border-red-300 bg-red-50 dark:bg-red-950/20"
          : "border-border bg-muted/30"
      }`}
    >
      <div className="flex items-center gap-1.5">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1.5 flex-1 text-left cursor-pointer"
        >
          <span className="text-xs">{icon}</span>
          <span className={`text-xs font-mono font-medium ${repeated ? "text-red-700 dark:text-red-400" : "text-foreground"}`}>
            {displayName}
          </span>
          {shortSummary && (
            <span className="text-xs text-muted-foreground">({shortSummary})</span>
          )}
          <span className="text-[10px] text-muted-foreground">{expanded ? "▲" : "▼"}</span>
        </button>
        {repeated && (
          <span className="text-[10px] bg-red-200 dark:bg-red-800 text-red-800 dark:text-red-200 px-1.5 py-0.5 rounded font-medium">
            重复调用
          </span>
        )}
      </div>
      {expanded && (
        <pre className="mt-1.5 p-2 bg-muted/50 rounded text-xs whitespace-pre-wrap overflow-auto max-h-[300px] font-mono leading-relaxed">
          {JSON.stringify(params, null, 2)}
        </pre>
      )}
    </div>
  );
}

/** 工具返回结果 */
function ToolResult({ content }: { content: string | ContentBlock[] }) {
  const [expanded, setExpanded] = useState(false);
  const text = extractText(content);

  // 清理 system 标签获取显示内容
  const displayText = text
    .replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, "")
    .trim();

  if (!displayText) return null;

  const needsExpand = displayText.length > 200;

  return (
    <div className="text-xs">
      <button
        onClick={() => setExpanded(!expanded)}
        className="text-muted-foreground hover:text-foreground cursor-pointer flex items-center gap-1"
      >
        <span className="font-medium">结果:</span>
        {!expanded && needsExpand && (
          <span className="text-foreground/70">{truncate(displayText, 120)}</span>
        )}
        {needsExpand && (
          <span className="text-[10px]">{expanded ? "收起 ▲" : "展开 ▼"}</span>
        )}
      </button>
      {(!needsExpand || expanded) && (
        <pre className="mt-1 p-2 bg-muted/30 rounded text-xs whitespace-pre-wrap overflow-auto max-h-[400px] font-mono leading-relaxed">
          {displayText}
        </pre>
      )}
    </div>
  );
}

/** 单条消息卡片 */
function MessageCard({
  msg,
  idx,
  assistantNumber,
  duplicateFlags,
  toolCallRange,
  defaultOpen,
}: {
  msg: MessageItem;
  idx: number;
  assistantNumber: number | undefined;
  duplicateFlags: boolean[];
  toolCallRange: [number, number] | undefined;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const config = ROLE_CONFIG[msg.role] ?? ROLE_CONFIG.user;
  const summary = buildSummary(msg, idx);

  // 提取内容
  const text = extractText(msg.content);
  const think = extractThink(msg.content);

  return (
    <div className={`border-l-2 ${config.borderColor} rounded-r-lg ${config.bgColor}`}>
      {/* 头部（始终可见） */}
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left cursor-pointer hover:bg-black/5 dark:hover:bg-white/5 rounded-r-lg transition-colors"
      >
        <span className={`text-xs font-semibold ${config.color}`}>
          {msg.role === "assistant" && assistantNumber
            ? `${config.label} #${assistantNumber}`
            : config.label}
        </span>
        <span className="flex-1 text-xs text-muted-foreground truncate">
          {!open ? summary : ""}
        </span>
        <span className="text-[10px] text-muted-foreground shrink-0">
          {open ? "▲" : "▼"}
        </span>
      </button>

      {/* 展开内容 */}
      {open && (
        <div className="px-3 pb-3 space-y-2">
          {/* 思考块 */}
          {think && <ThinkBlock text={think} />}

          {/* 文本内容 */}
          {text && msg.role === "user" && (
            <div className="text-sm whitespace-pre-wrap">{text}</div>
          )}
          {text && msg.role === "assistant" && (
            <div className="text-sm whitespace-pre-wrap">{text}</div>
          )}

          {/* 工具调用 */}
          {msg.tool_calls && msg.tool_calls.length > 0 && (
            <div className="space-y-1.5">
              {msg.tool_calls.map((tc, tcIdx) => {
                const globalIdx = toolCallRange ? toolCallRange[0] + tcIdx : -1;
                const repeated = globalIdx >= 0 ? duplicateFlags[globalIdx] : false;
                return (
                  <ToolCallItem key={tc.id} tc={tc} repeated={repeated} />
                );
              })}
            </div>
          )}

          {/* 工具结果 */}
          {msg.role === "tool" && <ToolResult content={msg.content} />}

          {/* System 全文 */}
          {msg.role === "system" && typeof msg.content === "string" && (
            <pre className="text-xs whitespace-pre-wrap bg-muted/30 p-3 rounded overflow-auto max-h-[400px] font-mono leading-relaxed">
              {msg.content}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================
// 主组件
// ============================================================

interface MessageChainProps {
  messages: MessageItem[];
}

export function MessageChain({ messages }: MessageChainProps) {
  const assistantNumbers = useAssistantNumbers(messages);

  // 全局重复检测
  const { toolCalls, messageToolCallRanges } = useMemo(
    () => buildGlobalToolCallIndex(messages),
    [messages]
  );
  const duplicateFlags = useMemo(
    () => computeDuplicateFlags(toolCalls),
    [toolCalls]
  );

  const [allExpanded, setAllExpanded] = useState(false);

  if (!messages || messages.length === 0) {
    return (
      <div className="flex items-center justify-center h-24 bg-muted/50 rounded-lg">
        <span className="text-sm text-muted-foreground">无对话记录</span>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {/* 展开/折叠全部 */}
      <div className="flex justify-end mb-1">
        <button
          onClick={() => setAllExpanded(!allExpanded)}
          className="text-xs px-2 py-1 rounded border hover:bg-accent transition-colors cursor-pointer"
        >
          {allExpanded ? "折叠全部 ▲" : "展开全部 ▼"}
        </button>
      </div>

      {messages.map((msg, idx) => {
        // System 消息默认折叠，第一条 User 消息和最后一个 Assistant 消息默认展开
        const defaultOpen = allExpanded
          ? true
          : msg.role === "system"
            ? false
            : idx === messages.findIndex((m) => m.role === "user")
              ? true
              : false;

        return (
          <MessageCard
            key={idx}
            msg={msg}
            idx={idx}
            assistantNumber={assistantNumbers.get(idx)}
            duplicateFlags={duplicateFlags}
            toolCallRange={messageToolCallRanges.get(idx)}
            defaultOpen={defaultOpen}
          />
        );
      })}
    </div>
  );
}
