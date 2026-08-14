import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AdminPage } from "./app/AdminPage";
import { AgentsPage } from "./app/AgentsPage";
import { AgentStudioPage } from "./app/AgentStudioPage";
import { AppShell } from "./app/AppShell";
import { ChatPage } from "./app/ChatPage";
import { FilesPage } from "./app/FilesPage";
import { LoginPage } from "./app/LoginPage";
import { SettingsPage } from "./app/SettingsPage";
import { SharePage } from "./app/SharePage";
import { useAuth } from "./stores/auth";
import { initTheme } from "./stores/ui";
import "./styles/tokens.css";

initTheme();
void useAuth.getState().boot();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/share/:token", element: <SharePage /> },
  {
    element: <AppShell />,
    children: [
      { path: "/", element: <ChatPage /> },
      { path: "/chat/:conversationId", element: <ChatPage /> },
      { path: "/agents", element: <AgentsPage /> },
      { path: "/agents/:agentId", element: <AgentStudioPage /> },
      { path: "/files", element: <FilesPage /> },
      { path: "/admin", element: <AdminPage /> },
      { path: "/settings", element: <SettingsPage /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
