# Third-party notices

## CC Switch

The Volcengine signing rules, response parsing behavior, and test fixtures in this
project are adapted from [farion1231/cc-switch](https://github.com/farion1231/cc-switch),
specifically `src-tauri/src/services/coding_plan.rs` at revision
`ef7b77dee255e2c3b9a594de454eae21476ca687`.

MIT License

Copyright (c) 2025 Jason Young

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Standalone macOS runtime

The macOS application also redistributes the following components. Their
copyright and license texts are included unmodified in the `licenses/`
directory, which is shipped inside the signed application under
`Contents/Resources/licenses/`.

- Python 3.13.9 (`Python-3.13.9.txt`).
- Certifi 2026.10.23, including the Mozilla CA bundle attribution and MPL 2.0
  notice (`Certifi-2026.10.23.txt`). The upstream CA source is identified in
  that notice; the unmodified bundle is shipped as `certifi/cacert.pem`.
- Requests 2.34.2, Charset Normalizer 3.5.2, IDNA 3.20, and urllib3 2.8.0.
- Keyring 25.7.0, Jaraco Classes 3.4.0, Jaraco Context 6.1.2, Jaraco
  Functools 4.6.0, More Itertools 11.2.1, Packaging 26.3, Pluggy 1.7.0, and
  Pygments 2.21.0.
- Pillow 12.3.0 (`Pillow-12.3.0-and-native-libraries.txt`). This upstream
  notice also covers the bundled Brotli, FreeType, HarfBuzz, JPEG, LCMS2,
  XZ/liblzma, OpenJPEG, libpng, libtiff, Xau, and XCB library notices.
- PyObjC Core, Cocoa, and Quartz 12.2.2, which share
  `PyObjC-12.2.2.txt`.
- Setuptools 80.10.2 and its vendored dependencies. The vendored notices are
  preserved by distribution name under `licenses/setuptools-vendored/`.
- The py2app 0.28.10 application bootstrap (`Py2app-0.28.10-runtime.txt`).
- OpenSSL 3.0.18 (`OpenSSL-3.0.18.txt`, Apache License 2.0), from
  <https://github.com/openssl/openssl/blob/openssl-3.0.18/LICENSE.txt>.
- zlib-ng 2.3.3 in zlib-compatible mode (`ZlibNG-2.3.3.md`), from
  <https://github.com/zlib-ng/zlib-ng/blob/2.3.3/LICENSE.md>.

Only runtime components present in the application are listed here; build-
and test-only tools are not included merely because they appear in the build
environment.
