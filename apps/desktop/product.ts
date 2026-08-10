import profile from "../../openppx/product.json";

export type AgentPrivilegeLevel = "low" | "medium" | "high" | "root";

export interface ProductProfile {
  productId: string;
  displayName: string;
  pythonDistributionName: string;
  cliCommand: string;
  cliAliases: string[];
  egressProxyCommand: string;
  npmWorkspaceName: string;
  desktopPackageName: string;
  desktopAppId: string;
  desktopArtifactName: string;
  nodeRootDirectory: string;
  workspaceStateDirectory: string;
  credentialService: string;
  serviceNamespace: string;
  defaultClientApiPort: number;
  environmentPrefix: string;
  defaultAgentId: string;
  defaultAgentDisplayName: string;
  allowedAgentPrivilegeLevels: AgentPrivilegeLevel[];
  defaultAgentPrivilegeLevel: AgentPrivilegeLevel;
  desktopAgentCreationEnabled: boolean;
  desktopUserDataDirectory: string;
}

/** Build-time product differences consumed by Electron and the Renderer. */
export const productProfile = profile as ProductProfile;
