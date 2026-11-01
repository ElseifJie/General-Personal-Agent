import { LayoutList, Package, X } from "lucide-react";
import type { ReactNode } from "react";

import { IconButton } from "../ui/index";
import { ArtifactPanel } from "./ArtifactPanel";
import type { DerivedArtifact } from "./derive";

/**
 * Voice conversation rail: the voice inspector and the artifacts produced by
 * the conversation. Tasks stay in the transcript; this panel intentionally has
 * only the two sections the voice surface needs.
 */
export function ConversationRail({
  artifacts,
  selectedArtifactId,
  onSelectArtifact,
  leading,
  onClose,
}: {
  artifacts: DerivedArtifact[];
  selectedArtifactId: string | null;
  onSelectArtifact: (artifactId: string) => void;
  /** Voice inspector block rendered above the artifacts section. */
  leading?: ReactNode;
  onClose: () => void;
}) {
  return (
    <aside
      id="gpa-detail-rail"
      className="gpa-rail-pane"
      aria-label="会话详情"
    >
      <header className="gpa-rail-pane__head">
        <h2 className="gpa-rail-pane__title">
          <LayoutList size={15} aria-hidden="true" />
          详情
        </h2>
        <IconButton label="收起侧栏" size="small" onClick={onClose}>
          <X size={15} aria-hidden="true" />
        </IconButton>
      </header>

      <div className="gpa-rail-pane__scroll">
        {leading}

        <section className="gpa-rail-section" aria-labelledby="gpa-rail-artifacts">
          <h3 className="gpa-rail-section__title" id="gpa-rail-artifacts">
            <Package size={14} aria-hidden="true" />
            产物
            <span className="gpa-rail-section__count">{artifacts.length}</span>
          </h3>
          <ArtifactPanel
            artifacts={artifacts}
            selectedArtifactId={selectedArtifactId}
            onSelectArtifact={onSelectArtifact}
          />
        </section>
      </div>
    </aside>
  );
}
