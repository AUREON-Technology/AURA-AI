// What the workspace, plan, billing and sharing routes answer (SaaS P1-F2). Parsing only.

import 'package:flutter/foundation.dart';

DateTime? _time(Object? value) => value is String && value.isNotEmpty ? DateTime.tryParse(value) : null;

int _int(Object? value) => value is num ? value.toInt() : int.tryParse('$value') ?? 0;

/// `EGP 150.00` from integer piastres.
String formatEgp(int piastres) {
  final pounds = piastres ~/ 100;
  final rest = (piastres % 100).toString().padLeft(2, '0');
  return 'EGP $pounds.$rest';
}

@immutable
class Member {
  const Member({required this.userId, required this.role, this.email, this.joinedAt});
  final String userId;
  final String role;
  final String? email;
  final DateTime? joinedAt;

  factory Member.fromJson(Map<String, dynamic> json) => Member(
        userId: '${json['user_id']}',
        role: '${json['role']}',
        email: json['email'] is String ? json['email'] as String : null,
        joinedAt: _time(json['joined_at']),
      );
}

@immutable
class InviteSummary {
  const InviteSummary({required this.inviteId, required this.email, required this.role, this.expiresAt});
  final String inviteId;
  final String email;
  final String role;
  final DateTime? expiresAt;

  factory InviteSummary.fromJson(Map<String, dynamic> json) => InviteSummary(
        inviteId: '${json['invite_id']}',
        email: '${json['email']}',
        role: '${json['role']}',
        expiresAt: _time(json['expires_at']),
      );
}

/// The token is present only when the server did not email it (email off).
@immutable
class CreatedInvite {
  const CreatedInvite({required this.invite, this.token});
  final InviteSummary invite;
  final String? token;
}

@immutable
class PlanUsage {
  const PlanUsage({
    required this.plan,
    required this.seats,
    required this.messagesUsed,
    required this.messagesAllowed,
    required this.storageUsed,
    required this.storageAllowed,
    required this.engineer,
    this.resetsAt,
    this.seatLimit,
  });

  final String plan;
  final int seats;
  final int messagesUsed;
  final int messagesAllowed;
  final int storageUsed;
  final int storageAllowed;
  final bool engineer;
  final DateTime? resetsAt;
  final int? seatLimit;

  factory PlanUsage.fromJson(Map<String, dynamic> json) => PlanUsage(
        plan: '${json['plan']}',
        seats: _int(json['seats']),
        messagesUsed: _int(json['messages_used']),
        messagesAllowed: _int(json['messages_allowed']),
        storageUsed: _int(json['storage_used']),
        storageAllowed: _int(json['storage_allowed']),
        engineer: json['engineer'] == true,
        resetsAt: _time(json['resets_at']),
        seatLimit: json['seat_limit'] == null ? null : _int(json['seat_limit']),
      );
}

@immutable
class SubscriptionView {
  const SubscriptionView({required this.plan, required this.seats, required this.status, this.periodEnd, this.graceUntil,
      this.provider = 'fawry'});
  final String plan;

  /// Who bills it: `fawry`, `apple` or `google`. Only the store can cancel its own.
  final String provider;
  final int seats;

  /// `active`, `past_due`, `grace`, `canceled`, `expired` or `pending`.
  final String status;
  final DateTime? periodEnd;
  final DateTime? graceUntil;

  factory SubscriptionView.fromJson(Map<String, dynamic> json) => SubscriptionView(
        plan: '${json['plan']}',
        seats: _int(json['seats']),
        status: '${json['status']}',
        periodEnd: _time(json['period_end']),
        graceUntil: _time(json['grace_until']),
        provider: json['provider'] is String ? json['provider'] as String : 'fawry',
      );

  /// Still billing or still giving access: a new purchase would charge twice.
  bool get live => const {'active', 'past_due', 'grace', 'canceled'}.contains(status);
  bool get fromStore => provider == 'apple' || provider == 'google';
}

@immutable
class PaymentView {
  const PaymentView({
    required this.id,
    required this.plan,
    required this.amountPiastres,
    required this.status,
    this.referenceNumber,
    this.createdAt,
    this.expiresAt,
    this.paidAt,
  });
  final String id;
  final String plan;
  final int amountPiastres;
  final String status;
  final String? referenceNumber;
  final DateTime? createdAt;
  final DateTime? expiresAt;
  final DateTime? paidAt;

  factory PaymentView.fromJson(Map<String, dynamic> json) => PaymentView(
        id: '${json['id']}',
        plan: '${json['plan']}',
        amountPiastres: _int(json['amount_piastres']),
        status: '${json['status']}',
        referenceNumber: json['reference_number'] as String?,
        createdAt: _time(json['created_at']),
        expiresAt: _time(json['expires_at']),
        paidAt: _time(json['paid_at']),
      );
}

@immutable
class BillingStatus {
  const BillingStatus({this.subscription, this.payments = const [], this.storeProducts = const {}});
  final SubscriptionView? subscription;
  final List<PaymentView> payments;

  /// The store products that sell Pro, by store (`apple`, `google`), from `AURA_STORE_PRODUCTS`.
  final Map<String, List<String>> storeProducts;

  factory BillingStatus.fromJson(Map<String, dynamic> json) => BillingStatus(
        subscription: json['subscription'] is Map<String, dynamic>
            ? SubscriptionView.fromJson(json['subscription'] as Map<String, dynamic>)
            : null,
        payments: [
          for (final p in (json['payments'] as List<dynamic>? ?? const []))
            if (p is Map<String, dynamic>) PaymentView.fromJson(p),
        ],
        storeProducts: {
          if (json['store_products'] is Map<String, dynamic>)
            for (final entry in (json['store_products'] as Map<String, dynamic>).entries)
              if (entry.value is List) entry.key: [for (final id in entry.value as List) if (id is String) id],
        },
      );
}

@immutable
class Checkout {
  const Checkout({required this.paymentId, required this.amountPiastres, this.referenceNumber, this.expiresAt});
  final String paymentId;
  final int amountPiastres;
  final String? referenceNumber;
  final DateTime? expiresAt;

  factory Checkout.fromJson(Map<String, dynamic> json) => Checkout(
        paymentId: '${json['payment_id']}',
        amountPiastres: _int(json['amount_piastres']),
        referenceNumber: json['reference_number'] as String?,
        expiresAt: _time(json['expires_at']),
      );
}

@immutable
class SharedChatSummary {
  const SharedChatSummary({required this.id, required this.title, required this.sharedBy, this.createdAt});
  final String id;
  final String title;
  final String sharedBy;
  final DateTime? createdAt;

  factory SharedChatSummary.fromJson(Map<String, dynamic> json) => SharedChatSummary(
        id: '${json['id']}',
        title: '${json['title']}',
        sharedBy: '${json['shared_by']}',
        createdAt: _time(json['created_at']),
      );
}

@immutable
class SharedChatMessage {
  const SharedChatMessage({required this.role, required this.text});
  final String role;
  final String text;
  Map<String, dynamic> toJson() => {'role': role, 'text': text};
}

@immutable
class SharedChat {
  const SharedChat({required this.summary, required this.messages});
  final SharedChatSummary summary;
  final List<SharedChatMessage> messages;

  factory SharedChat.fromJson(Map<String, dynamic> json) => SharedChat(
        summary: SharedChatSummary.fromJson(json),
        messages: [
          for (final m in (json['messages'] as List<dynamic>? ?? const []))
            if (m is Map<String, dynamic>) SharedChatMessage(role: '${m['role']}', text: '${m['text']}'),
        ],
      );
}
