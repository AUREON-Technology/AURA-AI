import 'dart:convert';
import 'package:crypto/crypto.dart';

/// Generates a stable, deterministic 64-bit unsigned integer ID string from a string using SHA-256.
String getDeterministicId(String value) {
  final bytes = utf8.encode(value);
  final digest = sha256.convert(bytes);
  
  // Extract the first 8 bytes and convert to a 64-bit unsigned integer
  final first8Bytes = digest.bytes.sublist(0, 8);
  int id = 0;
  for (int i = 0; i < 8; i++) {
    id = (id << 8) | first8Bytes[i];
  }
  
  return id.toUnsigned(64).toString();
}
