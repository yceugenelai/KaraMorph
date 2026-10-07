import unittest
from unittest.mock import patch, Mock
from scripts.bootstrap_models import main


class BootstrapModelsTests(unittest.TestCase):
    def test_check_does_not_download_missing_group(self):
        store = Mock()
        store.ready.return_value = False
        with patch('scripts.bootstrap_models.ModelStore', return_value=store):
            self.assertEqual(main(['check', '--group', 'separation']), 2)
        store.prepare.assert_not_called()

    def test_prepare_checks_completion(self):
        store = Mock()
        store.ready.return_value = True
        with patch('scripts.bootstrap_models.ModelStore', return_value=store):
            self.assertEqual(main(['prepare', '--group', 'styling']), 0)
        self.assertEqual(store.prepare.call_args.args, (['styling'],))

    def test_model_error_is_not_reported_as_success(self):
        store = Mock()
        store.prepare.side_effect = OSError('network interrupted')
        with patch('scripts.bootstrap_models.ModelStore', return_value=store):
            with self.assertRaisesRegex(OSError, 'network interrupted'):
                main(['prepare', '--group', 'separation'])
        store.ready.assert_not_called()
