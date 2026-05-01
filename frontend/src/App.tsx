import { Route, Routes } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { UserBadge } from "./components/UserBadge";
import { Workspace } from "./pages/Workspace";
import { Bookshelf } from "./pages/Bookshelf";
import { Profile } from "./pages/Profile";
import { BookDetail } from "./pages/BookDetail";

function Layout({ children }: { children: React.ReactNode }) {
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
    <Layout>
      <Routes>
        <Route path="/" element={<Workspace />} />
        <Route path="/bookshelf" element={<Bookshelf />} />
        <Route path="/profile" element={<Profile />} />
        <Route path="/book/:id" element={<BookDetail />} />
      </Routes>
    </Layout>
  );
}
