import { Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { ThemeProvider } from "./theme/ThemeContext";
import { Workspace } from "./pages/Workspace";
import { Bookshelf } from "./pages/Bookshelf";
import { Profile } from "./pages/Profile";
import { BookDetail } from "./pages/BookDetail";
import { CoverBookshelf } from "./pages/CoverBookshelf";
import { LoginPage } from "./pages/Login";
import { RegisterPage } from "./pages/Register";
import { WeReadTool } from "./pages/WeReadTool";
import { DataAnalysis } from "./pages/DataAnalysis";
import { PublicDomainRewrite } from "./pages/PublicDomainRewrite";
import { EpicNovelWorkspace } from "./pages/EpicNovelWorkspace";
import { LongNovelWorkspace } from "./pages/LongNovelWorkspace";
import { AigcReview } from "./pages/AigcReview";
import { TestingEndpoint } from "./pages/TestingEndpoint";
import { VideoScriptTable } from "./pages/VideoScriptTable";
import { NotFound } from "./pages/NotFound";
import { LONGFORM_VARIANT_CATEGORY_IDS } from "./constants/categories";

function ProtectedShell() {
  return (
    <ProtectedRoute>
      <AppShell />
    </ProtectedRoute>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <Routes>
          {/* 公共路由 */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />

          {/* 受保护路由：共用一个外壳，切页时侧栏不重建 */}
          <Route element={<ProtectedShell />}>
            <Route path="/" element={<Workspace />} />
            <Route path="/bookshelf" element={<Bookshelf />} />
            <Route
              path="/bookshelf/longform-variants"
              element={
                <Bookshelf
                  title="长文新版书架"
                  subtitle="收纳核心矛盾、困境自救、认知升级、因果哲思、独特观点、日常痛点等新版长文"
                  categoryScopeIds={LONGFORM_VARIANT_CATEGORY_IDS}
                  allCategoryLabel="全部新版"
                  emptyTitle="新版书架还空着"
                  emptyDescription="用新版分类生成书稿后，会自动收纳到这里"
                />
              }
            />
            <Route path="/covers" element={<CoverBookshelf />} />
            <Route path="/profile" element={<Profile />} />
            <Route path="/weread" element={<WeReadTool />} />
            <Route path="/aigc-review" element={<AigcReview />} />
            <Route path="/testing-endpoint" element={<TestingEndpoint />} />
            <Route
              path="/aigc-review/bookshelf"
              element={
                <Bookshelf
                  title="AI 审稿书架"
                  subtitle="自动保存原稿、审稿候选、质量闸门与真实朱雀复检状态"
                  categoryScopeIds={["aigc_review"]}
                  allCategoryLabel="全部审稿"
                  emptyTitle="审稿书架还空着"
                  emptyDescription="在 AI 审稿降率页面完成一次定向审稿后，稿件会自动归档到这里"
                  showCreateButton={false}
                />
              }
            />
            <Route path="/public-domain-rewrite" element={<PublicDomainRewrite fixedMode="full" />} />
            <Route
              path="/public-domain-classical-vernacular"
              element={<PublicDomainRewrite fixedMode="classical_vernacular" />}
            />
            <Route
              path="/public-domain-classical-vernacular/:taskId"
              element={<PublicDomainRewrite fixedMode="classical_vernacular" />}
            />
            <Route path="/long-novel" element={<LongNovelWorkspace />} />
            <Route path="/long-novel/:id" element={<LongNovelWorkspace />} />
            <Route path="/epic-novel" element={<EpicNovelWorkspace />} />
            <Route path="/epic-novel/:id" element={<EpicNovelWorkspace />} />
            <Route path="/video-script-table" element={<VideoScriptTable />} />
            <Route path="/data-analysis" element={<DataAnalysis />} />
            <Route path="/book/:id" element={<BookDetail />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </AuthProvider>
    </ThemeProvider>
  );
}
