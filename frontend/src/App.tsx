import React from 'react';
import ReportPage from './components/ReportPage';

// Механизм получения токенов полностью убран из фронтенда.
// Вся работа с Keycloak инкапсулирована в бэкенд-сервисе bionicpro-auth.
// Фронтенд общается только с bionicpro-auth и полагается на HttpOnly
// сессионную cookie, которую браузер отправляет автоматически.
const App: React.FC = () => {
  return (
    <div className="App">
      <ReportPage />
    </div>
  );
};

export default App;
