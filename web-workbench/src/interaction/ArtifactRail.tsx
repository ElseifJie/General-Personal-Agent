import { Package, X } from "lucide-react";

import { IconButton } from "../ui/index";
import { ArtifactPanel } from "./ArtifactPanel";
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
    <aside id="gpa-artifact-rail" className="gpa-rail-pane" aria-label="会话产物">
      <header className="gpa-rail-pane__head">
        <h2 className="gpa-rail-pane__title">
          <Package size={15} aria-hidden="true" />
          产物
          <span className="gpa-rail-section__count">{artifacts.length}</span>
        </h2>
        <IconButton label="收起侧栏" size="small" onClick={onClose}>
          <X size={15} aria-hidden="true" />
        </IconButton>
      </header>

      <div className="gpa-rail-pane__scroll">
        <ArtifactPanel
          artifacts={artifacts}
          selectedArtifactId={selectedArtifactId}
          onSelectArtifact={onSelectArtifact}
        />
      </div>
    </aside>
  );
}
