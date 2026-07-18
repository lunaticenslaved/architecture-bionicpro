import React, { useState, useEffect } from 'react';

// Базовый URL сервиса аутентификации bionicpro-auth.
const AUTH_URL = process.env.REACT_APP_AUTH_URL || 'http://localhost:8000';

interface UserInfo {
  username: string;
  email: string;
  roles: string[];
  identity_provider?: string | null;
  needs_consent?: boolean;
}

interface ReportData {
  subject: string;
  username: string;
  period: { from: string; to: string };
  days: number;
  processed_up_to: string;
  rows: Array<{
    event_date: string;
    prosthesis_serial: string;
    model: string;
    firmware_version: string;
    display_name: string;
    email: string;
    events_count: number;
    avg_response_ms: number;
    p95_response_ms: number;
    max_response_ms: number;
    slow_events_count: number;
    avg_signal_quality: number;
    min_battery_level: number;
    movements_count: number;
    first_event_at: string;
    last_event_at: string;
  }>;
  total_rows: number;
}

const ReportPage: React.FC = () => {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [checking, setChecking] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ReportData | null>(null);

  // Загружает информацию о пользователе (в т.ч. флаг needs_consent).
  const loadUser = async () => {
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

  // Проверяем наличие активной сессии через bionicpro-auth.
  // Токены на фронтенд не приходят — только факт авторизации.
  useEffect(() => {
    loadUser();
  }, []);

  // Обычный логин через Keycloak (LDAP/локальные пользователи).
  const login = () => {
    window.location.href = `${AUTH_URL}/auth/login`;
  };

  // Логин через внешний IdP Яндекс (Identity Brokering).
  const loginYandex = () => {
    window.location.href = `${AUTH_URL}/auth/login?idp=yandex`;
  };

  const logout = async () => {
    await fetch(`${AUTH_URL}/auth/logout`, {
      method: 'POST',
      credentials: 'include'
    });
    setUser(null);
  };

  // Отправляет решение пользователя по согласию на обработку данных.
  // При согласии bionicpro-auth заберёт профиль у Яндекса и сохранит в CRM.
  const submitConsent = async (granted: boolean) => {
    try {
      setLoading(true);
      setError(null);
      const resp = await fetch(`${AUTH_URL}/auth/consent`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ granted })
      });
      if (!resp.ok) {
        throw new Error(`Consent failed: ${resp.status}`);
      }
      // Обновляем состояние пользователя (needs_consent станет false).
      await loadUser();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  };

  const loadReport = async () => {
    try {
      setLoading(true);
      setError(null);
      setReport(null);

      // Запрос идёт через прокси bionicpro-auth. Никаких токенов в заголовках:
      // сервис сам подставит Bearer access_token из серверной сессии.
      // Reports API вернёт отчёт ТОЛЬКО по текущему пользователю (sub из JWT)
      // и только за период, уже обработанный Airflow (ETL watermark).
      const response = await fetch(`${AUTH_URL}/api/reports`, {
        credentials: 'include'
      });

      if (response.status === 401) {
        setUser(null);
        setError('Session expired. Please log in again.');
        return;
      }
      if (response.status === 403) {
        setError(
          'Доступ запрещён: отчёты доступны только пользователям протезов ' +
          '(роль prothetic_user) и только по собственным данным.'
        );
        return;
      }
      if (response.status === 409) {
        // ETL (Airflow) ещё не подготовил витрину — данных пока нет в OLAP.
        setError(
          'Отчёт ещё не готов: данные обрабатываются (ETL). ' +
          'Попробуйте позже.'
        );
        return;
      }
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }

      const data: ReportData = await response.json();
      setReport(data);
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
      <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100 gap-3">
        <button
          onClick={login}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 w-56"
        >
          Login
        </button>
        <button
          onClick={loginYandex}
          className="px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600 w-56"
        >
          Войти через Яндекс ID
        </button>
      </div>
    );
  }

  // Экран запроса согласия: показывается, когда пользователь вошёл через Яндекс,
  // но ещё не разрешил сервису использовать данные профиля.
  if (user.needs_consent) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
        <div className="p-8 bg-white rounded-lg shadow-md max-w-md">
          <h1 className="text-xl font-bold mb-4">Разрешение на использование данных</h1>
          <p className="mb-6 text-gray-700">
            Сервис протезов BionicPRO запрашивает у Яндекса данные вашего профиля
            (имя, e-mail, логин), чтобы связать их с вашей учётной записью.
            Разрешаете использовать и сохранить эти данные?
          </p>
          <div className="flex gap-3">
            <button
              onClick={() => submitConsent(true)}
              disabled={loading}
              className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
            >
              Разрешаю
            </button>
            <button
              onClick={() => submitConsent(false)}
              disabled={loading}
              className="px-4 py-2 bg-gray-200 rounded hover:bg-gray-300 disabled:opacity-50"
            >
              Не разрешаю
            </button>
          </div>
          {error && (
            <div className="mt-4 p-4 bg-red-100 text-red-700 rounded">
              {error}
            </div>
          )}
        </div>
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

        <p className="mb-4 text-gray-600">
          Signed in as {user.username}
          {user.identity_provider === 'yandex' && ' (через Яндекс ID)'}
        </p>

        <button
          onClick={loadReport}
          disabled={loading}
          className={`px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 ${
            loading ? 'opacity-50 cursor-not-allowed' : ''
          }`}
        >
          {loading ? 'Загрузка отчёта...' : 'Получить отчёт'}
        </button>

        {error && (
          <div className="mt-4 p-4 bg-red-100 text-red-700 rounded">
            {error}
          </div>
        )}

        {report && (
          <div className="mt-6 w-full">
            <h2 className="text-xl font-bold mb-2">Отчёт о работе протеза</h2>
            <p className="text-gray-600 mb-1">
              Период: {report.period.from} — {report.period.to} ({report.days} дней)
            </p>
            <p className="text-gray-600 mb-1">
              Данные актуальны на: {report.processed_up_to}
            </p>
            <p className="text-gray-600 mb-4">
              Всего записей: {report.total_rows}
            </p>

            {report.rows.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="min-w-full border-collapse border border-gray-300 text-sm">
                  <thead>
                    <tr className="bg-gray-100">
                      <th className="border p-2">Дата</th>
                      <th className="border p-2">Серийный №</th>
                      <th className="border p-2">Модель</th>
                      <th className="border p-2">Событий</th>
                      <th className="border p-2">Ср. ответ (мс)</th>
                      <th className="border p-2">P95 (мс)</th>
                      <th className="border p-2">Макс (мс)</th>
                      <th className="border p-2">Медленные</th>
                      <th className="border p-2">Качество сигнала</th>
                      <th className="border p-2">Батарея мин %</th>
                      <th className="border p-2">Движений</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.rows.map((row, idx) => (
                      <tr key={idx} className="hover:bg-gray-50">
                        <td className="border p-2">{row.event_date}</td>
                        <td className="border p-2">{row.prosthesis_serial}</td>
                        <td className="border p-2">{row.model}</td>
                        <td className="border p-2">{row.events_count}</td>
                        <td className="border p-2">{row.avg_response_ms?.toFixed(1)}</td>
                        <td className="border p-2">{row.p95_response_ms?.toFixed(1)}</td>
                        <td className="border p-2">{row.max_response_ms?.toFixed(1)}</td>
                        <td className="border p-2">{row.slow_events_count}</td>
                        <td className="border p-2">{row.avg_signal_quality?.toFixed(3)}</td>
                        <td className="border p-2">{row.min_battery_level}</td>
                        <td className="border p-2">{row.movements_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-gray-500 italic">Нет данных за выбранный период.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default ReportPage;
