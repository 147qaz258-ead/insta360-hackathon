import unittest

from scene.models import SceneUnderstanding


class SceneUnderstandingTests(unittest.TestCase):
    def test_normalizes_scene_and_deduplicates_prompts(self):
        scene = SceneUnderstanding.from_dict({
            "version": 1,
            "content_type": "photo",
            "scene_summary": "test",
            "regions": [{
                "id": "dog_right",
                "name": "dog",
                "importance": 1.2,
                "structural_importance": -1,
                "grounding_query": "dog",
            }],
            "relations": [],
            "grounding_prompts": ["dog", "Dog", "table"],
        })
        self.assertEqual(scene.regions[0].importance, 1.0)
        self.assertEqual(scene.regions[0].structural_importance, 0.0)
        self.assertEqual(scene.grounding_prompts, ["dog", "table"])


if __name__ == "__main__":
    unittest.main()
