/// Web build of `hostd_bundled_path.dart` -- there is no native daemon to bundle on web.
library;

String? bundledHostdPath({String? appExecutable, bool Function(String path)? exists}) => null;
