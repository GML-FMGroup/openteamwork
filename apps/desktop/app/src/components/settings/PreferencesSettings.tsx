import type {
  DesktopPreferenceChanges,
  DesktopPreferences,
} from "../../lib/desktop-preferences";

interface PreferencesSettingsProps {
  preferences: DesktopPreferences;
  onChange: (changes: DesktopPreferenceChanges) => void;
  onRequestNotificationPermission: () => Promise<NotificationPermission | "unsupported">;
}

const COMMON_TIMEZONES = [
  "UTC",
  "Asia/Shanghai",
  "Asia/Tokyo",
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Los_Angeles",
];

/** Device-local appearance, regional formatting, and attention preferences. */
export function PreferencesSettings({ preferences, onChange, onRequestNotificationPermission }: PreferencesSettingsProps) {
  async function enableNotifications(): Promise<void> {
    const permission = await onRequestNotificationPermission();
    onChange({ notificationsEnabled: permission === "granted" });
  }

  const timezones = COMMON_TIMEZONES.includes(preferences.timezone)
    ? COMMON_TIMEZONES
    : [preferences.timezone, ...COMMON_TIMEZONES];

  return (
    <section className="settings-card settings-page-card settings-card-preferences">
      <div className="settings-card-heading">
        <div>
          <h3>Preferences</h3>
          <p>Stored on this device. These choices never change Node, Agent, or Automation business data.</p>
        </div>
        <small>Revision {preferences.revision}</small>
      </div>

      <div className="preference-group">
        <div className="preference-group-copy"><h4>Appearance</h4><p>Use the system appearance or choose a stable theme and density.</p></div>
        <div className="settings-form preference-grid">
          <label className="settings-field"><span>Theme</span><select value={preferences.theme} onChange={(event) => onChange({ theme: event.target.value as DesktopPreferences["theme"] })}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
          <label className="settings-field"><span>Density</span><select value={preferences.density} onChange={(event) => onChange({ density: event.target.value as DesktopPreferences["density"] })}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label>
          <label className="preference-toggle"><input type="checkbox" checked={preferences.codeWrap} onChange={(event) => onChange({ codeWrap: event.target.checked })} /><span><strong>Wrap code and long tool output</strong><small>Disable to preserve horizontal scrolling.</small></span></label>
          <label className="settings-field"><span>Activity detail</span><select value={preferences.activityDetail} onChange={(event) => onChange({ activityDetail: event.target.value as DesktopPreferences["activityDetail"] })}><option value="summary">Summary</option><option value="detailed">Detailed</option></select></label>
        </div>
      </div>

      <div className="preference-group">
        <div className="preference-group-copy"><h4>Language & region</h4><p>The current release uses English. Display timezone never changes a schedule's stored timezone.</p></div>
        <div className="settings-form preference-grid">
          <label className="settings-field"><span>Interface language</span><select value={preferences.locale} disabled><option value="en">English (current release)</option></select></label>
          <label className="settings-field"><span>Display timezone</span><select value={preferences.timezone} onChange={(event) => onChange({ timezone: event.target.value })}>{timezones.map((timezone) => <option key={timezone} value={timezone}>{timezone}</option>)}</select></label>
        </div>
      </div>

      <div className="preference-group">
        <div className="preference-group-copy"><h4>Attention</h4><p>Desktop alerts are separate from Automation delivery and never grant execution permission.</p></div>
        <div className="settings-form preference-grid">
          <label className="preference-toggle"><input type="checkbox" checked={preferences.notificationsEnabled} onChange={(event) => event.target.checked ? void enableNotifications() : onChange({ notificationsEnabled: false })} /><span><strong>Desktop notifications</strong><small>Goal or Automation completion, failure, and approval waits.</small></span></label>
          <label className="preference-toggle"><input type="checkbox" checked={preferences.notificationSound} disabled={!preferences.notificationsEnabled} onChange={(event) => onChange({ notificationSound: event.target.checked })} /><span><strong>Notification sound</strong><small>Silent monitors remain silent.</small></span></label>
          <label className="settings-field"><span>When closing the window</span><select value={preferences.backgroundBehavior} onChange={(event) => onChange({ backgroundBehavior: event.target.value as DesktopPreferences["backgroundBehavior"] })}><option value="confirm-before-close">Ask before closing active work</option><option value="keep-running">Keep Node work running</option></select></label>
        </div>
      </div>
    </section>
  );
}
