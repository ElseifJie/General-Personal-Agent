import { Package } from "lucide-react";

import { ArtifactPanel } from "./ArtifactPanel";
import { RailPane } from "./RailPane";
import type { DerivedArtifact } from "./derive";

export function ArtifactRail({
  artifacts,
  selectedArtifactId,
  onSelectArtifact,
  onClose,
}: {
  artifacts: DerivedArtifact[];
  selectedArtifactId: string | null;
  onSelectArtifact: (artifactId: string) => void;
  onClose: () => void;
}) {
  return (
    <RailPane
      id="gpa-artifact-rail"
      label="会话产物"
      title="产物"
      icon={<Package size={15} aria-hidden="true" />}
      count={artifacts.length}
      onClose={onClose}
    >
      <ArtifactPanel
        artifacts={artifacts}
        selectedArtifactId={selectedArtifactId}
        onSelectArtifact={onSelectArtifact}
      />
    </RailPane>
  );
}
