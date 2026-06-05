import { BrowserRouter, Routes, Route, Navigate } from "react-router";
import { SWRConfig } from "swr";
import { Layout } from "./components/Layout";
import { Dashboard } from "./pages/Dashboard";
import { Compare } from "./pages/Compare";
import { Trend } from "./pages/Trend";
import { CaseDetail } from "./pages/CaseDetail";

/** 主应用组件，配置路由和全局 SWR 自动刷新（15 秒） */
export function App() {
  return (
    <SWRConfig value={{ refreshInterval: 15000 }}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/dashboard/:runId" element={<Dashboard />} />
            <Route path="/compare" element={<Compare />} />
            <Route path="/trend" element={<Trend />} />
            <Route path="/case/:runId/:caseId" element={<CaseDetail />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SWRConfig>
  );
}
