import { Route, Routes } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { UserBadge } from "./components/UserBadge";
import { Toaster } from "./components/Toaster";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { Workspace } from "./pages/Workspace";
import { Bookshelf } from "./pages/Bookshelf";
import { Profile } from "./pages/Profile";
import { BookDetail } from "./pages/BookDetail";
import { CoverBookshelf } from "./pages/CoverBookshelf";
import { LoginPage } from "./pages/Login";
import { RegisterPage } from "./pages/Register";
import { WeReadTool } from "./pages/WeReadTool";
import { DataAnalysis } from "./pages/DataAnalysis";
import { EpicNovelWorkspace } from "./pages/EpicNovelWorkspace";
import { LongNovelWorkspace } from "./pages/LongNovelWorkspace";
import { LONGFORM_VARIANT_CATEGORY_IDS } from "./constants/categories";

function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-base-900">
      <Sidebar />
      <main className="relative ml-[220px] min-h-screen px-10 py-10">
        <UserBadge />
        <div className="mx-auto max-w-[1400px] pt-8">{children}</div>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Toaster />
      <Routes>
        {/* 公共路由 */}
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        {/* 受保护路由：需要登录 */}
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <AppShell>
                <Workspace />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/bookshelf"
          element={
            <ProtectedRoute>
              <AppShell>
                <Bookshelf key="shelf-v1" title="1.0书架" bookshelfVersion="1.0" />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/bookshelf/v2"
          element={
            <ProtectedRoute><AppShell>
              <Bookshelf key="shelf-v2" title="2.0书架" bookshelfVersion="2.0"
                subtitle="社科2610 · 社会痛点2610"
                emptyDescription="选择智能体创建书稿，成稿会收纳在2.0书架" />
            </AppShell></ProtectedRoute>
          }
        />
        <Route
          path="/bookshelf/longform-variants"
          element={
            <ProtectedRoute>
              <AppShell>
                <Bookshelf
                  title="长文新版书架"
                  subtitle="收纳核心矛盾、困境自救、认知升级、因果哲思、独特观点、日常痛点等新版长文"
                  categoryScopeIds={LONGFORM_VARIANT_CATEGORY_IDS}
                  allCategoryLabel="全部新版"
                  emptyTitle="新版书架还空着"
                  emptyDescription="用新版分类生成书稿后，会自动收纳到这里"
                />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/covers"
          element={
            <ProtectedRoute>
              <AppShell>
                <CoverBookshelf />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/profile"
          element={
            <ProtectedRoute>
              <AppShell>
                <Profile />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/weread"
          element={
            <ProtectedRoute>
              <AppShell>
                <WeReadTool />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/long-novel"
          element={
            <ProtectedRoute>
              <AppShell>
                <LongNovelWorkspace />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/long-novel/:id"
          element={
            <ProtectedRoute>
              <AppShell>
                <LongNovelWorkspace />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/epic-novel"
          element={
            <ProtectedRoute>
              <AppShell>
                <EpicNovelWorkspace />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/epic-novel/:id"
          element={
            <ProtectedRoute>
              <AppShell>
                <EpicNovelWorkspace />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/data-analysis"
          element={
            <ProtectedRoute>
              <AppShell>
                <DataAnalysis />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/book/v2/:id"
          element={
            <ProtectedRoute><AppShell><BookDetail bookshelfVersion="2.0" /></AppShell></ProtectedRoute>
          }
        />
        <Route
          path="/book/:id"
          element={
            <ProtectedRoute>
              <AppShell>
                <BookDetail />
              </AppShell>
            </ProtectedRoute>
          }
        />
      </Routes>
    </AuthProvider>
  );
}
