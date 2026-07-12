import React, { useState, useEffect } from 'react';

// Базовый URL сервиса аутентификации bionicpro-auth.
const AUTH_URL = process.env.REACT_APP_AUTH_URL || 'http://localhost:8000';

interface UserInfo {
  username: string;
  email: string;
  roles: string[];
}

const ReportPage: React.FC = () => {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [checking, setChecking] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Проверяем наличие активной сессии через bionicpro-auth.
  // Токены на фронтенд не приходят — только факт авторизации.
  useEffect(() => {
    const checkSession = async () => {
      try {
        const resp = await fetch(`${AUTH_URL}/auth/userinfo`, {
          // credentials: 'include' обязателен, чтобы браузер отправил
          // и принял HttpOnly сессионную cookie.
          credentials: 'include'
        });
        if (resp.ok) {
          setUser(await resp.json());
        } else {
          setUser(null);
        }
      } catch {
        setUser(null);
      } finally {
        setChecking(false);
      }
    };
    checkSession();
  }, []);

  // Логин — редирект на bionicpro-auth, который запускает PKCE-флоу.
  const login = () => {
    window.location.href = `${AUTH_URL}/auth/login`;
  };

  const logout = async () => {
    await fetch(`${AUTH_URL}/auth/logout`, {
      method: 'POST',
      credentials: 'include'
    });
    setUser(null);
  };

  const downloadReport = async () => {
    try {
      setLoading(true);
      setError(null);

      // Запрос идёт через прокси bionicpro-auth. Никаких токенов в заголовках:
      // сервис сам подставит Bearer access_token из серверной сессии.
      const response = await fetch(`${AUTH_URL}/api/reports`, {
        credentials: 'include'
      });

      if (response.status === 401) {
        setUser(null);
        setError('Session expired. Please log in again.');
        return;
      }
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'prosthesis-report.csv';
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  };

  if (checking) {
    return <div>Loading...</div>;
  }

  if (!user) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
        <button
          onClick={login}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
        >
          Login
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
      <div className="p-8 bg-white rounded-lg shadow-md">
        <div className="flex justify-between items-center mb-6">
          <h1 className="text-2xl font-bold">Usage Reports</h1>
          <button
            onClick={logout}
            className="px-3 py-1 text-sm bg-gray-200 rounded hover:bg-gray-300"
          >
            Logout
          </button>
        </div>

        <p className="mb-4 text-gray-600">Signed in as {user.username}</p>

        <button
          onClick={downloadReport}
          disabled={loading}
          className={`px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 ${
            loading ? 'opacity-50 cursor-not-allowed' : ''
          }`}
        >
          {loading ? 'Generating Report...' : 'Download Report'}
        </button>

        {error && (
          <div className="mt-4 p-4 bg-red-100 text-red-700 rounded">
            {error}
          </div>
        )}
      </div>
    </div>
  );
};

export default ReportPage;
