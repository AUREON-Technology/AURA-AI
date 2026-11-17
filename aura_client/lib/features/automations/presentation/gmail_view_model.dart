import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:extension_google_sign_in_as_googleapis_auth/extension_google_sign_in_as_googleapis_auth.dart';

import 'package:aura_client/l10n/l10n.dart';

import '../../calendar/data/calendar_repository.dart';
import '../../tasks/data/task_repository.dart';
import '../data/automation_repository.dart';

@immutable
class GmailUiState {
  final bool loading;
  final List<GmailMessageHeader> emails;
  final Map<String, EmailAnalysisResult> analyses;
  final Set<String> analyzingIds;

  const GmailUiState({
    this.loading = false,
    this.emails = const [],
    this.analyses = const {},
    this.analyzingIds = const {},
  });

  GmailUiState copyWith({
    bool? loading,
    List<GmailMessageHeader>? emails,
    Map<String, EmailAnalysisResult>? analyses,
    Set<String>? analyzingIds,
  }) {
    return GmailUiState(
      loading: loading ?? this.loading,
      emails: emails ?? this.emails,
      analyses: analyses ?? this.analyses,
      analyzingIds: analyzingIds ?? this.analyzingIds,
    );
  }
}

class GmailViewModel extends Notifier<GmailUiState> {
  static const _gmailScope = 'https://www.googleapis.com/auth/gmail.readonly';

  @override
  GmailUiState build() => const GmailUiState();

  Future<String?> fetchUnread() async {
    state = state.copyWith(loading: true, emails: const []);
    try {
      final googleSignIn = GoogleSignIn.instance;
      final scopes = [_gmailScope];
      final authorization = await googleSignIn.authorizationClient.authorizeScopes(scopes);
      final client = authorization.authClient(scopes: scopes);
      final emails = await ref.read(automationRepositoryProvider).fetchUnreadEmails(client);
      state = state.copyWith(loading: false, emails: emails);
      return null;
    } catch (e) {
      state = state.copyWith(loading: false);
      return currentL10n.automationsGmailFetchError('$e');
    }
  }

  Future<String?> analyzeEmail(GmailMessageHeader email) async {
    state = state.copyWith(analyzingIds: {...state.analyzingIds, email.id});
    try {
      final result = await ref.read(automationRepositoryProvider).analyzeEmail(email);
      final analyses = Map<String, EmailAnalysisResult>.from(state.analyses)..[email.id] = result;
      final analyzing = Set<String>.from(state.analyzingIds)..remove(email.id);
      state = state.copyWith(analyses: analyses, analyzingIds: analyzing);
      return null;
    } catch (e) {
      final analyzing = Set<String>.from(state.analyzingIds)..remove(email.id);
      state = state.copyWith(analyzingIds: analyzing);
      return currentL10n.automationsGmailAnalysisFailed('$e');
    }
  }

  Future<void> importActionItem(EmailActionItem item, {required bool urgent, required String category}) {
    return ref.read(taskRepositoryProvider).addTask(
          title: item.title,
          priority: urgent ? 'high' : 'medium',
          category: category.toLowerCase(),
          dueDate: item.dueDate,
        );
  }

  Future<void> importSuggestedEvent(EmailAnalysisResult analysis) {
    return ref.read(calendarRepositoryProvider).addEvent(
          title: analysis.eventTitle!,
          startTime: analysis.eventStart!,
          endTime: analysis.eventEnd ?? analysis.eventStart!.add(const Duration(hours: 1)),
        );
  }
}

final gmailViewModelProvider = NotifierProvider<GmailViewModel, GmailUiState>(GmailViewModel.new);
