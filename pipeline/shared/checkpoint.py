"""Checkpoint management for pipeline resume/progress tracking."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .constants import ARTIFACTS


class Checkpoint:
    """
    Track pipeline step progress for resume capability.

    Usage:
        checkpoint = Checkpoint.load("Step2_ClassifyPages", stage=2)

        for item in items:
            if item.id in checkpoint.completed_items:
                continue  # Already done

            checkpoint.mark_in_progress(item.id)
            # ... process item ...
            checkpoint.complete(item.id)

        checkpoint.finalize()
    """

    def __init__(
        self,
        step_name: str,
        stage: int,
        checkpoint_path: Optional[Path] = None,
    ):
        self.step_name = step_name
        self.stage = stage
        # Use step-specific checkpoint filename (e.g., step1_checkpoint.json)
        step_num = step_name.lower().replace("step", "").split("_")[0]
        default_path = ARTIFACTS / f"stage{stage}" / f"step{step_num}_checkpoint.json"
        self.checkpoint_path = checkpoint_path or default_path

        self.started_at: str = datetime.now().isoformat()
        self.updated_at: str = self.started_at
        self.status: str = "in_progress"

        self.total_items: int = 0
        self.completed_count: int = 0
        self.failed_count: int = 0
        self.skipped_count: int = 0

        self.completed_items: Set[str] = set()
        self.failed_items: Dict[str, str] = {}  # item_id -> error message
        self.current_item: Optional[str] = None

        self._save_interval = 100  # Save every N completions
        self._pending_saves = 0

    @classmethod
    def load(cls, step_name: str, stage: int) -> "Checkpoint":
        """Load existing checkpoint or create new one."""
        step_num = step_name.lower().replace("step", "").split("_")[0]
        checkpoint_path = ARTIFACTS / f"stage{stage}" / f"step{step_num}_checkpoint.json"

        if checkpoint_path.exists():
            try:
                data = json.loads(checkpoint_path.read_text())
                if data.get("step") == step_name and data.get("status") == "in_progress":
                    # Resume from existing checkpoint
                    instance = cls(step_name, stage, checkpoint_path)
                    instance.started_at = data.get("started_at", instance.started_at)
                    instance.updated_at = data.get("updated_at", instance.updated_at)
                    instance.status = data.get("status", "in_progress")

                    progress = data.get("progress", {})
                    instance.total_items = progress.get("total_items", 0)
                    instance.completed_count = progress.get("completed", 0)
                    instance.failed_count = progress.get("failed", 0)
                    instance.skipped_count = progress.get("skipped", 0)

                    instance.completed_items = set(data.get("completed_items", []))
                    instance.failed_items = data.get("failed_items", {})
                    instance.current_item = data.get("resume_from")

                    print(f"Resuming {step_name} from checkpoint: {instance.completed_count}/{instance.total_items}")
                    return instance
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Warning: Could not load checkpoint ({e}), starting fresh")

        # Create new checkpoint
        instance = cls(step_name, stage, checkpoint_path)
        return instance

    def set_total(self, total: int) -> None:
        """Set total items to process."""
        self.total_items = total
        self._save()

    def mark_in_progress(self, item_id: str) -> None:
        """Mark an item as currently being processed."""
        self.current_item = item_id

    def complete(self, item_id: str) -> None:
        """Mark an item as successfully completed."""
        self.completed_items.add(item_id)
        self.completed_count += 1
        self.updated_at = datetime.now().isoformat()
        self.current_item = None

        self._pending_saves += 1
        if self._pending_saves >= self._save_interval:
            self._save()
            self._pending_saves = 0

    def fail(self, item_id: str, error: str) -> None:
        """Mark an item as failed with error message."""
        self.failed_items[item_id] = error
        self.failed_count += 1
        self.updated_at = datetime.now().isoformat()
        self.current_item = None
        self._save()

    def skip(self, item_id: str) -> None:
        """Mark an item as skipped (already done or excluded)."""
        self.skipped_count += 1

    def finalize(self) -> None:
        """Mark checkpoint as completed and save."""
        self.status = "completed"
        self.updated_at = datetime.now().isoformat()
        self._save()
        print(f"Completed {self.step_name}: {self.completed_count} done, {self.failed_count} failed, {self.skipped_count} skipped")

    def _save(self) -> None:
        """Write checkpoint to disk."""
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "step": self.step_name,
            "stage": self.stage,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "progress": {
                "total_items": self.total_items,
                "completed": self.completed_count,
                "failed": self.failed_count,
                "skipped": self.skipped_count,
            },
            "completed_items": list(self.completed_items),
            "failed_items": self.failed_items,
            "resume_from": self.current_item,
        }

        self.checkpoint_path.write_text(json.dumps(data, indent=2))

    def to_dict(self) -> Dict[str, Any]:
        """Export checkpoint state as dictionary."""
        return {
            "step": self.step_name,
            "stage": self.stage,
            "status": self.status,
            "progress": {
                "total_items": self.total_items,
                "completed": self.completed_count,
                "failed": self.failed_count,
                "skipped": self.skipped_count,
            },
        }
