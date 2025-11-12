import { useEffect } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import Notes from './pages/Notes';
import Reminders from './pages/Reminders';
import Passwords from './pages/Passwords';
import Photos from './pages/Photos';
import Schedules from './pages/Schedules';
import Logs from './pages/Logs';
import Settings from './pages/Settings';
import FirstRunWizard from './components/FirstRunWizard';
import { useAppStore } from './store/app-store';
import apiClient from './lib/api-client';

function App() {
  const { setIsFirstRun, wizardCompleted, theme, setTheme } = useAppStore();

  // Apply theme on initial load
  useEffect(() => {
    setTheme(theme);
  }, [theme, setTheme]);

  useEffect(() => {
    // Check if this is first run by trying to load config
    const checkFirstRun = async () => {
      try {
        // If wizard was already completed, we're not in first run
        if (wizardCompleted) {
          console.log('Wizard already completed, skipping first run');
          setIsFirstRun(false);
          return;
        }

        // Try to fetch config to see if app has been configured
        const config = await apiClient.getConfig();
        console.log('Config loaded:', config);

        // If at least one service is configured with its required settings, consider app configured
        const isConfigured =
          (config.notes_enabled && config.notes_remote_folder) ||
          (config.reminders_enabled && config.reminders_caldav_url && config.reminders_caldav_username) ||
          (config.passwords_enabled && config.passwords_vaultwarden_url && config.passwords_vaultwarden_email);

        if (isConfigured) {
          console.log('Found configured services, marking wizard as complete');
          setIsFirstRun(false);
        } else {
          console.log('No services configured, showing first run wizard');
          setIsFirstRun(true);
        }
      } catch (err) {
        // If config fetch fails, assume first run
        console.error('Failed to check first run status:', err);
        setIsFirstRun(true);
      }
    };

    checkFirstRun();
  }, [setIsFirstRun, wizardCompleted]);

  return (
    <BrowserRouter>
      <FirstRunWizard />
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="notes" element={<Notes />} />
          <Route path="reminders" element={<Reminders />} />
          <Route path="passwords" element={<Passwords />} />
          <Route path="photos" element={<Photos />} />
          <Route path="schedules" element={<Schedules />} />
          <Route path="logs" element={<Logs />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
