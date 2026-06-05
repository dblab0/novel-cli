import { Outlet } from "react-router";
import { Sidebar } from "./Sidebar";

/** 主布局组件，包含侧边栏和内容区域 */
export function Layout() {
  return (
    <div className="flex h-screen bg-background">
      <Sidebar />
      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
