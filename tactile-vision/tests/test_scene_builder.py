import unittest

from scene.models import (
    GroundedRegion,
    GroundedScene,
    SceneRegion,
    SceneUnderstanding,
    TactilePlan,
)
from planner.builder import TactileSceneBuilder


def make_scene_understanding():
    return SceneUnderstanding.from_dict({
        "version": 1,
        "content_type": "photo",
        "scene_summary": "一个人和一只狗",
        "regions": [
            {"id": "person_left", "name": "person", "kind": "person"},
            {"id": "dog_right", "name": "dog", "kind": "animal"},
        ],
        "relations": [
            {"subject": "dog_right", "relation": "right_of", "object": "person_left"}
        ],
    })


def make_plan(regions):
    return TactilePlan.from_dict({
        "version": 1,
        "global_strategy": "outline primary, support background",
        "regions": regions,
        "preserve_relations": ["person_left left_of dog_right"],
    })


class TactileSceneBuilderTests(unittest.TestCase):
    def test_approx_bbox_normalized_and_contoured(self):
        scene = TactileSceneBuilder().build(
            make_scene_understanding(),
            make_plan([
                {
                    "id": "person_left",
                    "tactile_role": "primary",
                    "approx_bbox": [50, 100, 450, 900],
                    "boundary_level": 3,
                },
            ]),
        )
        self.assertEqual(len(scene.regions), 1)
        region = scene.regions[0]
        self.assertEqual(region.source, "approximate")
        self.assertEqual(region.boundary_level, 3)
        contour = region.contours[0]
        self.assertEqual(
            contour,
            [[0.05, 0.1], [0.45, 0.1], [0.45, 0.9], [0.05, 0.9]],
        )
        self.assertEqual(scene.metrics["rendered_regions"], 1)
        self.assertEqual(scene.metrics["geometry_sources"]["approximate"], 1)

    def test_zero_to_one_bbox_also_accepted(self):
        scene = TactileSceneBuilder().build(
            make_scene_understanding(),
            make_plan([
                {"id": "dog_right", "approx_bbox": [0.5, 0.4, 0.95, 0.9]},
            ]),
        )
        self.assertEqual(
            scene.regions[0].contours[0],
            [[0.5, 0.4], [0.95, 0.4], [0.95, 0.9], [0.5, 0.9]],
        )

    def test_missing_geometry_reported_not_invented(self):
        scene = TactileSceneBuilder().build(
            make_scene_understanding(),
            make_plan([
                {"id": "person_left", "boundary_level": 3},
                {"id": "dog_right", "boundary_level": 2},
            ]),
        )
        self.assertEqual(scene.metrics["unplaced_regions"], 2)
        self.assertEqual(scene.metrics["unplaced_ids"], ["person_left", "dog_right"])
        self.assertEqual(len(scene.warnings), 2)
        for region in scene.regions:
            self.assertEqual(region.source, "unplaced")
            self.assertEqual(region.contours, [])

    def test_grounded_scene_geometry_wins(self):
        grounded = GroundedScene(
            image_width=800,
            image_height=600,
            regions=[
                GroundedRegion(
                    id="person_left",
                    bbox_xyxy=[80.0, 60.0, 360.0, 540.0],
                    source="rdk_yoloworld",
                )
            ],
        )
        scene = TactileSceneBuilder().build(
            make_scene_understanding(),
            make_plan([
                {
                    "id": "person_left",
                    "approx_bbox": [50, 100, 450, 900],
                    "boundary_level": 3,
                },
            ]),
            grounded_scene=grounded,
        )
        region = scene.regions[0]
        self.assertEqual(region.source, "grounded")
        self.assertEqual(
            region.contours[0],
            [[0.1, 0.1], [0.45, 0.1], [0.45, 0.9], [0.1, 0.9]],
        )

    def test_role_defaults_and_suppression(self):
        scene = TactileSceneBuilder().build(
            make_scene_understanding(),
            make_plan([
                {"id": "person_left", "tactile_role": "primary", "approx_bbox": [10, 10, 400, 900]},
                {
                    "id": "dog_right",
                    "tactile_role": "background_structure",
                    "approx_bbox": [0, 0, 1000, 1000],
                    "boundary_level": 0,
                    "internal_level": 0,
                },
            ]),
        )
        person = scene.regions[0]
        dog = scene.regions[1]
        self.assertEqual(person.boundary_level, 3)
        self.assertEqual(person.internal_level, 2)
        self.assertEqual(dog.boundary_level, 0)
        self.assertEqual(dog.source, "unplaced")

    def test_relations_fallback_to_scene_relations(self):
        plan = make_plan([])
        plan.preserve_relations = []
        scene = TactileSceneBuilder().build(make_scene_understanding(), plan)
        self.assertEqual(scene.relations, ["dog_right right_of person_left"])


if __name__ == "__main__":
    unittest.main()
