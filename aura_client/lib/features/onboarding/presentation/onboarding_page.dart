import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

import 'onboarding_gate.dart';

class OnboardingPage extends ConsumerStatefulWidget {
  const OnboardingPage({super.key});

  @override
  ConsumerState<OnboardingPage> createState() => _OnboardingPageState();
}

class _OnboardingPageState extends ConsumerState<OnboardingPage> {
  final _controller = PageController();
  int _index = 0;

  static const _pageCount = 3;

  List<(IconData, String, String)> _pages(BuildContext context) {
    final l10n = context.l10n;
    return [
      (
        Icons.auto_awesome_outlined,
        l10n.onboardingCopilotTitle,
        l10n.onboardingCopilotBody,
      ),
      (
        Icons.cloud_outlined,
        l10n.onboardingPrivacyTitle,
        l10n.onboardingPrivacyBody,
      ),
      (
        Icons.view_week_outlined,
        l10n.onboardingTourTitle,
        l10n.onboardingTourBody,
      ),
    ];
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _finish() async {
    await ref.read(onboardingGateProvider.notifier).complete();
    if (mounted) context.go('/today');
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Scaffold(
      backgroundColor: palette.background,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(AuraSpacing.xl),
          child: Column(
            children: [
              Align(
                alignment: AlignmentDirectional.centerEnd,
                child: AnimatedOpacity(
                  opacity: _index == _pageCount - 1 ? 0 : 1,
                  duration: AuraMotion.respecting(context, AuraMotion.fast),
                  child: TextButton(
                    onPressed: _index == _pageCount - 1 ? null : _finish,
                    child: Text(context.l10n.onboardingSkip),
                  ),
                ),
              ),
              Expanded(
                child: PageView.builder(
                  controller: _controller,
                  itemCount: _pageCount,
                  onPageChanged: (i) => setState(() => _index = i),
                  itemBuilder: (context, i) {
                    final page = _pages(context)[i];
                    return Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        ExcludeSemantics(
                          child: Container(
                            width: 112,
                            height: 112,
                            decoration: BoxDecoration(
                              gradient: palette.brandGradient,
                              borderRadius: BorderRadius.circular(
                                AuraRadius.xl + 8,
                              ),
                              boxShadow: [
                                BoxShadow(
                                  color: palette.brand.withValues(alpha: 0.35),
                                  blurRadius: 36,
                                  offset: const Offset(0, 14),
                                ),
                              ],
                            ),
                            child: Icon(
                              page.$1,
                              size: 52,
                              color: palette.onBrand,
                            ),
                          ),
                        ),
                        const SizedBox(height: AuraSpacing.xxl),
                        Text(
                          page.$2,
                          style: Theme.of(context).textTheme.headlineMedium
                              ?.copyWith(fontWeight: FontWeight.w700),
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: AuraSpacing.md),
                        ConstrainedBox(
                          constraints: const BoxConstraints(maxWidth: 420),
                          child: Text(
                            page.$3,
                            style: Theme.of(context).textTheme.bodyLarge
                                ?.copyWith(
                                  color: palette.textSecondary,
                                  height: 1.5,
                                ),
                            textAlign: TextAlign.center,
                          ),
                        ),
                      ],
                    );
                  },
                ),
              ),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  for (var i = 0; i < _pageCount; i++)
                    AnimatedContainer(
                      duration: AuraMotion.respecting(
                        context,
                        AuraMotion.standard,
                      ),
                      curve: AuraMotion.standardCurve,
                      width: i == _index ? 24 : 8,
                      height: 8,
                      margin: const EdgeInsets.symmetric(horizontal: 4),
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(AuraRadius.pill),
                        gradient: i == _index ? palette.brandGradient : null,
                        color: i == _index ? null : palette.border,
                      ),
                    ),
                ],
              ),
              const SizedBox(height: AuraSpacing.xl),
              AuraButton(
                label: _index == _pageCount - 1
                    ? context.l10n.onboardingGetStarted
                    : context.l10n.onboardingNext,
                expand: true,
                onPressed: () {
                  if (_index == _pageCount - 1) {
                    _finish();
                  } else {
                    _controller.nextPage(
                      duration: AuraMotion.respecting(
                        context,
                        AuraMotion.standard,
                      ),
                      curve: AuraMotion.standardCurve,
                    );
                  }
                },
              ),
            ],
          ),
        ),
      ),
    );
  }
}
