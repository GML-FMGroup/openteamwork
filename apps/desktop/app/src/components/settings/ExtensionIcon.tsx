import type { CSSProperties, ReactNode } from "react";
import type { SimpleIcon } from "simple-icons";
import {
  siAsana,
  siBox,
  siBrave,
  siClickup,
  siConfluence,
  siDiscord,
  siDropbox,
  siFigma,
  siGithub,
  siGitlab,
  siGmail,
  siGooglecalendar,
  siGoogledrive,
  siHubspot,
  siJira,
  siLinear,
  siMixpanel,
  siNotion,
  siPosthog,
  siPostman,
  siQuickbooks,
  siStripe,
  siSupabase,
  siTelegram,
  siWhatsapp,
  siZendesk,
} from "simple-icons";
import type { ExtensionPresentation, ExtensionSummary } from "../../types";

interface IconEntry {
  color?: string;
  mark: () => ReactNode;
}

function simpleIcon(icon: SimpleIcon): IconEntry {
  return {
    color: `#${icon.hex}`,
    mark: () => (
      <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d={icon.path} />
      </svg>
    ),
  };
}

function monogram(value: string): IconEntry {
  return { mark: () => <span className="extension-icon-monogram">{value}</span> };
}

function strokeMark(children: ReactNode): IconEntry {
  return {
    mark: () => (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {children}
      </svg>
    ),
  };
}

const TYPE_ICONS: Record<ExtensionSummary["kind"], IconEntry> = {
  plugin: strokeMark(<><path d="M8 4v4M16 4v4M8 16v4M16 16v4M4 8h4M16 8h4M4 16h4M16 16h4" /><rect x="8" y="8" width="8" height="8" rx="2" /></>),
  app: strokeMark(<><rect x="4" y="4" width="16" height="16" rx="4" /><path d="M8 8h3v3H8zM13 8h3v3h-3zM8 13h3v3H8zM13 13h3v3h-3z" /></>),
  mcp: strokeMark(<><circle cx="12" cy="12" r="2.2" /><circle cx="5" cy="6" r="1.8" /><circle cx="19" cy="6" r="1.8" /><circle cx="12" cy="20" r="1.8" /><path d="m10.3 10.6-3.9-3.3m7.3 3.3 3.9-3.3M12 14.2v4" /></>),
  skill: strokeMark(<><path d="M6 4.5h9a3 3 0 0 1 3 3v12H9a3 3 0 0 1-3-3z" /><path d="M9 7.5h6M9 11h6M9 14.5h4" /></>),
};

const ICONS: Record<string, IconEntry> = {
  telegram: simpleIcon(siTelegram),
  slack: monogram("S"),
  email: strokeMark(<><rect x="3" y="5" width="18" height="14" rx="2.5" /><path d="m3.5 7.5 8.5 6 8.5-6" /></>),
  gmail: simpleIcon(siGmail),
  "google-calendar": simpleIcon(siGooglecalendar),
  github: simpleIcon(siGithub),
  outlook: monogram("O"),
  jira: simpleIcon(siJira),
  monday: strokeMark(<><path d="M5 7h8M5 12h14M5 17h6" /></>),
  confluence: simpleIcon(siConfluence),
  zendesk: simpleIcon(siZendesk),
  linear: simpleIcon(siLinear),
  gitlab: simpleIcon(siGitlab),
  discord: simpleIcon(siDiscord),
  stripe: simpleIcon(siStripe),
  asana: simpleIcon(siAsana),
  hubspot: simpleIcon(siHubspot),
  dropbox: simpleIcon(siDropbox),
  box: simpleIcon(siBox),
  whatsapp: simpleIcon(siWhatsapp),
  quickbooks: simpleIcon(siQuickbooks),
  docusign: monogram("D"),
  clickup: simpleIcon(siClickup),
  "google-drive": simpleIcon(siGoogledrive),
  canva: monogram("C"),
  figma: simpleIcon(siFigma),
  close: monogram("CL"),
  notion: simpleIcon(siNotion),
  attio: strokeMark(<><path d="m5 19 7-14 7 14M8 13h8" /></>),
  posthog: simpleIcon(siPosthog),
  mixpanel: simpleIcon(siMixpanel),
  amplitude: strokeMark(<path d="M2.5 13.5h4l3-8 4.5 13 3-8h4.5" />),
  apollo: strokeMark(<><circle cx="10" cy="14" r="6" /><path d="m14.5 9.5 6.5-6.5M16.5 3H21v4.5" /></>),
  hunter: monogram("H"),
  granola: monogram("G"),
  browserbase: monogram("B"),
  firecrawl: monogram("F"),
  exa: monogram("E"),
  "brave-search": simpleIcon(siBrave),
  postman: simpleIcon(siPostman),
  supabase: simpleIcon(siSupabase),
  "parallel-search": monogram("P"),
  playwright: monogram("P"),
  context7: monogram("C7"),
  "microsoft-learn": monogram("ML"),
  "aws-docs": monogram("AWS"),
};

function toRgba(hex: string, alpha: number): string {
  const normalized = hex.replace(/^#/, "");
  if (!/^[0-9a-fA-F]{6}$/.test(normalized)) return "rgba(107, 114, 128, 0.12)";
  const red = Number.parseInt(normalized.slice(0, 2), 16);
  const green = Number.parseInt(normalized.slice(2, 4), 16);
  const blue = Number.parseInt(normalized.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

/** Render one local, deterministic Extension icon with a safe type fallback. */
export function ExtensionIcon({
  presentation,
  kind,
  label,
}: {
  presentation: ExtensionPresentation;
  kind: ExtensionSummary["kind"];
  label: string;
}) {
  const entry = ICONS[presentation.icon] ?? TYPE_ICONS[kind];
  const color = presentation.brandColor ?? entry.color ?? "#6b7280";
  const style = {
    "--extension-icon-color": color,
    "--extension-icon-soft": toRgba(color, 0.12),
  } as CSSProperties;
  return (
    <span
      className="extension-icon-badge"
      data-extension-icon={ICONS[presentation.icon] ? presentation.icon : kind}
      title={label}
      style={style}
      aria-hidden="true"
    >
      {entry.mark()}
    </span>
  );
}
