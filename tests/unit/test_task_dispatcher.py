from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from learning_apps.infrastructure.services.task_dispatcher import dispatch_task, dispatch_task_by_name


class NamedTaskDispatcherTests(SimpleTestCase):
    @override_settings(
        LEARNING_USE_CELERY=False,
        LEARNING_LOCAL_TASKS_INLINE=None,
    )
    @patch("learning_apps.infrastructure.services.task_dispatcher.threading.Thread")
    def test_sqlite_fallback_runs_inline_to_avoid_concurrent_writer(self, thread_cls) -> None:
        fallback = Mock()

        with patch("learning_apps.infrastructure.services.task_dispatcher.close_old_connections"):
            accepted = dispatch_task(None, fallback=fallback)

        self.assertFalse(accepted)
        fallback.assert_called_once_with()
        thread_cls.assert_not_called()

    @override_settings(
        LEARNING_USE_CELERY=False,
        LEARNING_LOCAL_TASKS_INLINE=False,
    )
    @patch("learning_apps.infrastructure.services.task_dispatcher.threading.Thread")
    def test_explicit_async_fallback_starts_background_thread(self, thread_cls) -> None:
        fallback = Mock()

        accepted = dispatch_task(None, fallback=fallback)

        self.assertFalse(accepted)
        fallback.assert_not_called()
        thread_cls.assert_called_once()
        thread_cls.return_value.start.assert_called_once_with()

    @override_settings(LEARNING_USE_CELERY=True)
    @patch("learning_apps.infrastructure.services.task_dispatcher.current_app.send_task")
    def test_dispatches_by_stable_task_name_without_importing_adapter(self, send_task) -> None:
        accepted = dispatch_task_by_name(
            "learning_goal.run_creation_job",
            "job-1",
            "student",
            raw_preference_text="Learn algebra",
        )

        self.assertTrue(accepted)
        send_task.assert_called_once_with(
            "learning_goal.run_creation_job",
            args=("job-1", "student"),
            kwargs={"raw_preference_text": "Learn algebra"},
        )
