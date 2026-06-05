import { useState, useMemo } from "react";
import { useNavigate, useLocation } from "react-router";
import {
  ChevronRight,
  ChevronDown,
  BookOpen,
  Bot,
  Layers,
  Play,
  GitCompare,
  TrendingUp,
} from "lucide-react";
import {
  useBooks,
  useAgents,
  useScenarios,
  useRuns,
} from "@/api/client";

/** 树节点类型 */
type NodeType = "book" | "agent" | "scenario" | "run";

/** 树节点结构 */
interface TreeNode {
  name: string;
  type: NodeType;
  expanded: boolean;
}

/** 书籍子树组件 - 提取公共渲染逻辑，避免 Sidebar 内重复代码 */
function BookTree({
  book,
  selectedBook,
  expandedBooks,
  expandedAgents,
  expandedScenarios,
  agents,
  scenarios,
  runs,
  currentRunId,
  onToggleBook,
  onToggleAgent,
  onToggleScenario,
  onSelectRun,
  indent,
}: {
  book: string;
  selectedBook: string | null;
  expandedBooks: Record<string, boolean>;
  expandedAgents: Record<string, boolean>;
  expandedScenarios: Record<string, boolean>;
  agents: string[] | undefined;
  scenarios: string[] | undefined;
  runs: { run_id: string; model: string; timestamp: string }[] | undefined;
  currentRunId: string | null;
  onToggleBook: (book: string) => void;
  onToggleAgent: (agent: string) => void;
  onToggleScenario: (scenario: string) => void;
  onSelectRun: (runId: string) => void;
  indent: string;
}) {
  return (
    <div className={indent}>
      {/* 书籍节点 */}
      <button
        className="flex items-center w-full px-2 py-1.5 text-sm rounded hover:bg-accent text-left gap-1.5"
        onClick={() => onToggleBook(book)}
      >
        {expandedBooks[book] ? (
          <ChevronDown className="w-4 h-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="w-4 h-4 shrink-0 text-muted-foreground" />
        )}
        <BookOpen className="w-4 h-4 shrink-0 text-muted-foreground" />
        <span className="truncate">{book}</span>
      </button>

      {/* Agent 列表 */}
      {expandedBooks[book] && selectedBook === book && agents?.map((agent) => {
        const agentKey = `${book}/${agent}`;
        return (
          <div key={agent} className="ml-4">
            <button
              className="flex items-center w-full px-2 py-1.5 text-sm rounded hover:bg-accent text-left gap-1.5"
              onClick={() => onToggleAgent(agent)}
            >
              {expandedAgents[agentKey] ? (
                <ChevronDown className="w-4 h-4 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="w-4 h-4 shrink-0 text-muted-foreground" />
              )}
              <Bot className="w-4 h-4 shrink-0 text-muted-foreground" />
              <span className="truncate">{agent}</span>
            </button>

            {/* Scenario 列表 */}
            {expandedAgents[agentKey] && scenarios?.map((scenario) => {
              const scenarioKey = `${book}/${agent}/${scenario}`;
              return (
                <div key={scenario} className="ml-4">
                  <button
                    className="flex items-center w-full px-2 py-1.5 text-sm rounded hover:bg-accent text-left gap-1.5"
                    onClick={() => onToggleScenario(scenario)}
                  >
                    {expandedScenarios[scenarioKey] ? (
                      <ChevronDown className="w-4 h-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="w-4 h-4 shrink-0 text-muted-foreground" />
                    )}
                    <Layers className="w-4 h-4 shrink-0 text-muted-foreground" />
                    <span className="truncate">{scenario}</span>
                  </button>

                  {/* Run 列表 */}
                  {expandedScenarios[scenarioKey] && runs?.map((run) => {
                    const isSelected = currentRunId === run.run_id;
                    return (
                      <button
                        key={run.run_id}
                        className={`flex items-center w-full ml-4 px-2 py-1.5 text-sm rounded text-left gap-1.5 ${
                          isSelected
                            ? "bg-accent font-medium"
                            : "hover:bg-accent"
                        }`}
                        onClick={() => onSelectRun(run.run_id)}
                      >
                        <Play className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
                        <span className="truncate">{run.run_id}</span>
                        <span className="ml-auto text-xs text-muted-foreground">
                          {run.model}
                        </span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

/** 侧边栏导航组件 - 四级导航树：书籍 -> Agent -> Scenario -> Run */
export function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();

  /** 各层级展开状态 */
  const [expandedBooks, setExpandedBooks] = useState<Record<string, boolean>>({});
  const [expandedAgents, setExpandedAgents] = useState<Record<string, boolean>>({});
  const [expandedScenarios, setExpandedScenarios] = useState<Record<string, boolean>>({});

  /** 当前选中的节点路径信息，用于加载子节点数据 */
  const [selectedBook, setSelectedBook] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [selectedScenario, setSelectedScenario] = useState<string | null>(null);

  /** 从 URL 中提取当前选中的 runId */
  const currentRunId = (() => {
    const match = location.pathname.match(/\/dashboard\/([^/]+)/);
    return match ? match[1] : null;
  })();

  /** 数据加载 */
  const { data: books } = useBooks();
  const { data: agents } = useAgents(selectedBook ?? "");
  const { data: scenarios } = useScenarios(selectedBook ?? "", selectedAgent ?? "");
  const { data: runs } = useRuns(selectedBook ?? "", selectedAgent ?? "", selectedScenario ?? "");

  /** 切换书籍展开状态 */
  const toggleBook = (book: string) => {
    setExpandedBooks((prev) => {
      const next = { ...prev };
      const wasExpanded = prev[book];
      next[book] = !wasExpanded;
      if (wasExpanded) {
        // 折叠时清空下级选择
        setSelectedBook(null);
        setSelectedAgent(null);
        setSelectedScenario(null);
      } else {
        setSelectedBook(book);
      }
      return next;
    });
  };

  /** 切换 Agent 展开状态 */
  const toggleAgent = (agent: string) => {
    setExpandedAgents((prev) => {
      const next = { ...prev };
      const key = `${selectedBook}/${agent}`;
      const wasExpanded = prev[key];
      next[key] = !wasExpanded;
      if (wasExpanded) {
        setSelectedAgent(null);
        setSelectedScenario(null);
      } else {
        setSelectedAgent(agent);
      }
      return next;
    });
  };

  /** 切换 Scenario 展开状态 */
  const toggleScenario = (scenario: string) => {
    setExpandedScenarios((prev) => {
      const next = { ...prev };
      const key = `${selectedBook}/${selectedAgent}/${scenario}`;
      const wasExpanded = prev[key];
      next[key] = !wasExpanded;
      if (wasExpanded) {
        setSelectedScenario(null);
      } else {
        setSelectedScenario(scenario);
      }
      return next;
    });
  };

  /** 点击 Run 节点，跳转到 Dashboard */
  const selectRun = (runId: string) => {
    navigate(`/dashboard/${runId}`);
  };

  /** 判断当前路径是否激活 */
  const isCompareActive = location.pathname === "/compare";
  const isTrendActive = location.pathname === "/trend";

  return (
    <aside className="w-72 border-r flex flex-col" style={{ backgroundColor: "oklch(0.96 0.005 280)" }}>
      {/* 标题栏 */}
      <div className="p-4 border-b">
        <h2 className="font-semibold text-sm">Eval Viewer</h2>
      </div>

      {/* 导航树 */}
      <nav className="flex-1 overflow-auto p-2">
        {books?.map((book) => (
          <BookTree
            key={book}
            book={book}
            selectedBook={selectedBook}
            expandedBooks={expandedBooks}
            expandedAgents={expandedAgents}
            expandedScenarios={expandedScenarios}
            agents={agents}
            scenarios={scenarios}
            runs={runs}
            currentRunId={currentRunId}
            onToggleBook={toggleBook}
            onToggleAgent={toggleAgent}
            onToggleScenario={toggleScenario}
            onSelectRun={selectRun}
            indent=""
          />
        ))}

        {/* 无数据提示 */}
        {(!books || books.length === 0) && (
          <div className="px-3 py-4 text-xs text-muted-foreground text-center">
            暂无评估数据
          </div>
        )}
      </nav>

      {/* 底部快捷链接 */}
      <div className="p-2 border-t space-y-1">
        <a
          href="/compare"
          className={`flex items-center gap-2 px-3 py-1.5 text-sm rounded ${
            isCompareActive ? "bg-accent font-medium" : "hover:bg-accent"
          }`}
        >
          <GitCompare className="w-4 h-4" />
          版本对比
        </a>
        <a
          href="/trend"
          className={`flex items-center gap-2 px-3 py-1.5 text-sm rounded ${
            isTrendActive ? "bg-accent font-medium" : "hover:bg-accent"
          }`}
        >
          <TrendingUp className="w-4 h-4" />
          时间趋势
        </a>
      </div>
    </aside>
  );
}
