import { Navigate, Outlet } from "react-router-dom";
import { Spinner } from "../components/ui";
import { useAuth } from "../stores/auth";
import { Sidebar } from "../features/chat/Sidebar";
import { SearchPalette } from "../features/search/SearchPalette";

export function AppShell() {
  const status = useAuth((s) => s.status);
  if (status === "booting") {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (status === "anon") return <Navigate to="/login" replace />;
  return (
    <div className="flex h-full">
      <SearchPalette />
      <Sidebar />
      <main className="min-w-0 flex-1">
        <Outlet />
      </main>
    </div>
  );
}
