"""Console entrypoint used by the Windows CLI distribution."""
from multiprocessing import freeze_support
import sys

if __name__ == '__main__':
    freeze_support()
    if sys.argv[1:2] == ['camera-soak']:
        from camera_acceptance import main as acceptance_main
        raise SystemExit(acceptance_main(sys.argv[2:]))
    if sys.argv[1:2] == ['environment-probe']:
        from environment_acceptance import main as environment_main
        raise SystemExit(environment_main(sys.argv[2:]))
    from gazespace.cli import main
    raise SystemExit(main())
