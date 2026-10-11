import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './store'
import PublicPage from './pages/PublicPage'
import LoginPage from './pages/LoginPage'
import TeacherWorkspace from './pages/TeacherWorkspace'
import AdminPanel from './pages/AdminPanel'
import ProfilePage from './pages/ProfilePage'

function ProtectedRoute({ children, role }: { children: React.ReactNode; role?: string }) {
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn)
  const user = useAuthStore((s) => s.user)
  if (!isLoggedIn) return <Navigate to="/login" replace />
  if (role && user && user.role?.toUpperCase() !== role.toUpperCase()) {
    return <Navigate to="/teacher" replace />
  }
  return <>{children}</>
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<PublicPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/teacher/*"
          element={
            <ProtectedRoute>
              <TeacherWorkspace />
            </ProtectedRoute>
          }
        />
        <Route
          path="/teacher/profile"
          element={
            <ProtectedRoute>
              <ProfilePage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin"
          element={
            <ProtectedRoute role="ADMIN">
              <AdminPanel />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
