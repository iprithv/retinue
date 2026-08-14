/** Pure UI state (§6.3 rule: nothing streaming passes through here). */
import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "light" | "dark" | "system";

interface UiState {
  theme: Theme;
  sidebarOpen: boolean;
  setTheme: (theme: Theme) => void;
  toggleSidebar: () => void;
}

function applyTheme(theme: Theme): void {
  const dark =
    theme === "dark" ||
    (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  // shiki's dual-theme output uses light-dark(), which follows color-scheme
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}

export const useUi = create<UiState>()(
  persist(
    (set, get) => ({
      theme: "system",
      sidebarOpen: true,
      setTheme: (theme) => {
        set({ theme });
        applyTheme(theme);
      },
      toggleSidebar: () => set({ sidebarOpen: !get().sidebarOpen }),
    }),
    { name: "retinue-ui" },
  ),
);

export function initTheme(): void {
  applyTheme(useUi.getState().theme);
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => applyTheme(useUi.getState().theme));
}
