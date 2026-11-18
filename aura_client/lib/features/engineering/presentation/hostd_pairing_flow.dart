/// Pairs the local `aura_hostd` with the account this app is signed in to (ADR-011 Unit 8).
///
/// One flow for every place that offers it -- the daemon page, the IDE's Host panel and the IDE
/// status bar's "Re-pair required" item (G-12) -- instead of two copies that had drifted apart and
/// both left the "Waiting for approval..." dialog on screen when a step failed.
library;

import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_error_codes.dart';
import 'package:aura_client/core/hostd/hostd_error_messages.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Runs the whole pairing: the daemon starts it and shows a code, this signed-in app approves it
/// (the daemon runs on this same machine, so there is no second device to approve from -- see
/// ADR-011's known gaps), then waits for the daemon to report the outcome.
///
/// The code dialog is closed on every way out -- approved, refused, expired, or any step
/// throwing -- and the outcome is said in a snackbar. [httpClient] is for tests.
Future<void> pairHostDaemon(BuildContext context, WidgetRef ref, {http.Client? httpClient}) async {
  final client = ref.read(hostdControlClientProvider);
  final navigator = Navigator.of(context, rootNavigator: true);
  var dialogOpen = false;
  void closeDialog() {
    if (!dialogOpen) return;
    dialogOpen = false;
    navigator.pop();
  }

  try {
    final backendUrl = ref.read(backendUrlProvider);
    final start = await client.startPairing(backendUrl: backendUrl);
    final userCode = start['user_code'] as String;
    final intervalS = (start['interval'] as num).toInt();
    final expiresIn = (start['expires_in'] as num).toInt();
    if (!context.mounted) return;

    dialogOpen = true;
    unawaited(showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => AuraDialog(
        title: dialogContext.l10n.engineeringPairThisDevice,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(dialogContext.l10n.engineeringPairApproving),
            const SizedBox(height: AuraSpacing.md),
            SelectableText(
              userCode,
              textAlign: TextAlign.center,
              textDirection: TextDirection.ltr,
              style: const TextStyle(fontFamily: 'Courier', fontSize: 22, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: AuraSpacing.lg),
            const Center(child: CircularProgressIndicator()),
            const SizedBox(height: AuraSpacing.sm),
            Text(dialogContext.l10n.engineeringPairWaiting, textAlign: TextAlign.center),
          ],
        ),
      ),
    ));

    final accessToken = await ref.read(tokenManagerProvider).getValidAccessToken();
    if (accessToken == null) {
      throw StateError(currentL10n.engineeringPairSignInFirst);
    }
    final uri = Uri.parse('$backendUrl/api/v3/auth/desktop/pair/approve');
    final headers = {'Authorization': 'Bearer $accessToken', 'Content-Type': 'application/json'};
    final body = jsonEncode({'user_code': userCode});
    final approveResponse = httpClient == null
        ? await http.post(uri, headers: headers, body: body)
        : await httpClient.post(uri, headers: headers, body: body);
    if (approveResponse.statusCode != 200) {
      throw StateError(
          currentL10n.engineeringPairApprovalRejected(approveResponse.statusCode, approveResponse.body));
    }
    // The daemon reports its hostname with the code (FSA-004); naming it here is what lets a
    // person notice a code that is not their machine's.
    final decoded = jsonDecode(approveResponse.body);
    final approvedName = decoded is Map ? decoded['device_name'] as String? : null;

    final deadline = DateTime.now().add(Duration(seconds: expiresIn));
    String? outcome;
    String? failureReason;
    while (context.mounted && DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(Duration(seconds: intervalS > 0 ? intervalS : 2));
      final status = await client.pairingStatus();
      final statusValue = status['status'] as String?;
      if (statusValue == 'approved') {
        outcome = 'approved';
        break;
      }
      if (statusValue != 'pending') {
        outcome = 'failed';
        // A newer daemon sends a `code` next to the English `reason` (hostd_error_codes.dart).
        final rawReason = status['reason'] as String? ?? statusValue;
        failureReason = rawReason == null ? null : hostdCodeMessage(status['code'] as String?, rawReason);
        break;
      }
    }

    closeDialog();
    if (outcome == 'approved') {
      ref.invalidate(hostdStatusProvider);
      if (context.mounted) {
        AuraSnackbar.show(
          context,
          message: approvedName == null
              ? context.l10n.engineeringPaired
              : context.l10n.engineeringPairedNamed(approvedName),
          kind: AuraSnackbarKind.success,
        );
      }
    } else if (context.mounted) {
      AuraSnackbar.show(
        context,
        message: outcome == 'failed'
            ? context.l10n.engineeringPairingFailed(failureReason ?? '')
            : context.l10n.engineeringPairingExpired,
        kind: AuraSnackbarKind.error,
      );
    }
  } catch (error) {
    // G-12: the dialog used to stay up here, spinning on "Waiting for approval..." forever.
    closeDialog();
    if (context.mounted) {
      AuraSnackbar.show(
        context,
        message: context.l10n.engineeringPairingStartFailed(
            error is StateError ? error.message : describeHostdError(error)),
        kind: AuraSnackbarKind.error,
      );
    }
  }
}
