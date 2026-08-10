import { describe, expect, it } from "vitest";
import workspaceManifest from "../../../package.json";
import desktopManifest from "../package.json";
import builderConfig from "../electron-builder.yml?raw";
import { productProfile } from "../product";

describe("OpenTeamwork product profile", () => {
  it("owns product-facing defaults without renaming the shared runtime", () => {
    expect(productProfile.productId).toBe("openteamwork");
    expect(productProfile.displayName).toBe("OpenTeamwork");
    expect(productProfile.pythonDistributionName).toBe("openteamwork");
    expect(productProfile.cliCommand).toBe("otw");
    expect(productProfile.cliAliases).toEqual(["openteamwork"]);
    expect(productProfile.egressProxyCommand).toBe("otw-egress-proxy");
    expect(productProfile.npmWorkspaceName).toBe("openteamwork");
    expect(productProfile.desktopPackageName).toBe("@openteamwork/desktop");
    expect(productProfile.desktopAppId).toBe("com.openteamwork.desktop");
    expect(productProfile.desktopArtifactName).toBe("OpenTeamwork-Desktop");
    expect(productProfile.nodeRootDirectory).toBe(".openteamwork");
    expect(productProfile.environmentPrefix).toBe("OPENTEAMWORK");
    expect(productProfile.defaultClientApiPort).toBe(18_765);
  });

  it("keeps the enterprise Agent policy", () => {
    expect(productProfile.allowedAgentPrivilegeLevels).toEqual(["low", "medium", "high", "root"]);
    expect(productProfile.defaultAgentPrivilegeLevel).toBe("medium");
    expect(productProfile.desktopAgentCreationEnabled).toBe(true);
  });

  it("keeps manifests aligned with the product profile", () => {
    expect(workspaceManifest.name).toBe(productProfile.npmWorkspaceName);
    expect(workspaceManifest.repository.url).toBe("https://github.com/pipixia-labs/openteamwork.git");
    expect(desktopManifest.name).toBe(productProfile.desktopPackageName);
    expect(builderConfig).toContain(`appId: ${productProfile.desktopAppId}`);
    expect(builderConfig).toContain(`productName: ${productProfile.displayName} Desktop`);
    expect(builderConfig).toContain(`artifactName: ${productProfile.desktopArtifactName}-`);
  });
});
