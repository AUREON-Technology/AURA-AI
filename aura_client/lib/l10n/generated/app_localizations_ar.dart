// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Arabic (`ar`).
class AppLocalizationsAr extends AppLocalizations {
  AppLocalizationsAr([String locale = 'ar']) : super(locale);

  @override
  String get chatRetryOnlyLatestTurn =>
      'فيه رسايل جت بعد دي، فمينفعش تعيد محاولتها من هنا. ابعتها تاني لو لسه محتاج رد.';

  @override
  String get chatImageCapabilityCheckFailed =>
      'معرفناش نتأكد إذا كان ينفع ترفق صور. دوس عشان تجرب تاني.';

  @override
  String get memorySaveQueued =>
      'اتحفظت على الجهاز ده. AURA هيفتكرها أول ما يوصل للسيرفر.';

  @override
  String memorySaveRefused(String fact) {
    return 'السيرفر مقبلش الذكرى دي: $fact';
  }

  @override
  String shareTruncatedOlder(int count, int omitted) {
    return 'المحادثة المتشاركة بتساع آخر $count رسالة بس، فالـ$omitted رسالة الأقدم مش هتتشارك.';
  }

  @override
  String get greetingMorning => 'صباح الخير';

  @override
  String get greetingAfternoon => 'نهارك سعيد';

  @override
  String get greetingEvening => 'مساء الخير';

  @override
  String get captureBarHint => 'اسأل AURA أي حاجة، أو سجّل مهمة…';

  @override
  String get dueToday => 'مطلوب النهارده';

  @override
  String get viewAll => 'عرض الكل';

  @override
  String get nothingDueToday => 'مفيش حاجة مطلوبة النهارده';

  @override
  String get allCaughtUp => 'إنت مخلّص كل حاجة.';

  @override
  String get couldNotLoadDueToday => 'مقدرناش نحمّل حاجات النهارده';

  @override
  String get activeTasks => 'المهام الشغالة';

  @override
  String get waterToday => 'المية النهارده';

  @override
  String get spentThisPeriod => 'المصروف في الفترة دي';

  @override
  String get aiSystemStatus => 'حالة نظام الذكاء الاصطناعي';

  @override
  String get serverInference => 'خدمة AURA AI';

  @override
  String get statusReady => 'جاهز';

  @override
  String get statusUnavailable => 'مش متاح';

  @override
  String get localData => 'البيانات المحلية';

  @override
  String get encryptedAesGcm => 'متشفرة (AES-256-GCM)';

  @override
  String get settingsTooltip => 'أنا';

  @override
  String get navToday => 'النهارده';

  @override
  String get navCopilot => 'Copilot';

  @override
  String get navLifeHub => 'حياتي';

  @override
  String get navYou => 'أنا';

  @override
  String get lifeHubTitle => 'حياتي';

  @override
  String get youTitle => 'أنا';

  @override
  String get knowledgeTitle => 'المعرفة';

  @override
  String get memoryTitle => 'الذاكرة';

  @override
  String get feedbackTitle => 'الملاحظات';

  @override
  String get appearanceTitle => 'المظهر';

  @override
  String get engineStatusTitle => 'حالة المحرك';

  @override
  String get onboardingNext => 'التالي';

  @override
  String get onboardingCopilotTitle => 'AURA هو الـ Copilot بتاعك';

  @override
  String get onboardingCopilotBody =>
      'كلّمه. هو بيفتكر، وبيدوّر في مستنداتك، وبيقترح إجراءات — وإنت اللي بتأكد أي تعديل.';

  @override
  String get onboardingPrivacyTitle => 'طلباتك بتتعالج فين';

  @override
  String get onboardingPrivacyBody =>
      'مفيش حاجة بتتعالج على الموبايل ده. مستنداتك وذكرياتك وملفاتك مبيقراهاش غير نماذج ذكاء اصطناعي AURA مشغّلها بنفسه. الاستثناء هو المهندس: بعد ما توافق في التطبيق، الرسايل اللي بتبعتها له، والملفات اللي بيفتحها، ونتيجة الأوامر اللي بيشغّلها بتروح لـ Claude، وده نموذج ذكاء اصطناعي بتشغّله Anthropic. الرسايل التانية ممكن يرد عليها مزوّد ذكاء اصطناعي خارجي معتمد، لو مشغّل السيرفر سامح بكده.';

  @override
  String get onboardingTourTitle => 'النهارده، Copilot، حياتي، أنا';

  @override
  String get onboardingTourBody =>
      'سجّل من أي مكان. تصفّح المهام والتقويم والماليات والصحة في «حياتي». أدوات الحساب موجودة تحت «أنا».';

  @override
  String get onboardingSkip => 'تخطّي';

  @override
  String get onboardingGetStarted => 'يلا نبدأ';

  @override
  String get notificationsTooltip => 'الإشعارات';

  @override
  String unreadNotifications(int count) {
    return 'إشعارات مش مقروءة: $count';
  }

  @override
  String get settingsSectionAccount => 'الحساب';

  @override
  String get settingsSectionDesktop => 'الديسكتوب';

  @override
  String get settingsSectionYourAura => 'AURA بتاعك';

  @override
  String get settingsSectionEngineer => 'المهندس';

  @override
  String get settingsSectionSystem => 'النظام';

  @override
  String get settingsSectionAbout => 'عن التطبيق';

  @override
  String get settingsProfileTitle => 'البروفايل';

  @override
  String get settingsProfileSubtitle => 'الحساب المسجّل دخوله والأمان';

  @override
  String get settingsAppearanceSubtitle => 'زي الجهاز، فاتح، أو غامق';

  @override
  String get serverEnvironmentTitle => 'بيئة السيرفر';

  @override
  String get settingsServerEnvironmentSubtitle => 'سيرفر الإنتاج أو التطوير';

  @override
  String get settingsDevicesSubtitle =>
      'تسجيلات الدخول من الديسكتوب والـ Daemons المربوطة بالمهندس';

  @override
  String get devicesTitle => 'أجهزة الديسكتوب';

  @override
  String get devicesIntro =>
      'كل تطبيق ديسكتوب مسجّل دخول على حسابك وكل Daemon للمهندس مربوط بيه. الغي أي واحد عشان يتسجّل خروجه على طول.';

  @override
  String get devicesEmpty => 'مفيش أجهزة ديسكتوب';

  @override
  String get devicesEmptyMessage =>
      'سجّل دخول من تطبيق الديسكتوب أو اربط Daemon للمهندس وهيظهر هنا.';

  @override
  String get devicesLoadFailed => 'مقدرناش نحمّل أجهزتك';

  @override
  String get devicesRetry => 'جرّب تاني';

  @override
  String get devicesUnnamed => 'جهاز من غير اسم';

  @override
  String get devicesKindApp => 'تطبيق ديسكتوب';

  @override
  String get devicesKindDaemon => 'Daemon المهندس';

  @override
  String get devicesKindUnknown => 'جهاز';

  @override
  String devicesPairedOn(String date) {
    return 'من $date';
  }

  @override
  String get devicesThisDevice => 'الجهاز ده';

  @override
  String get devicesRevoke => 'إلغاء الوصول';

  @override
  String get devicesKeep => 'سيبه';

  @override
  String get devicesRevokeConfirmTitle => 'عايز تلغي وصول الجهاز ده؟';

  @override
  String devicesRevokeConfirmMessage(String name) {
    return '$name هيتسجّل خروجه فورًا، ولازم يسجّل دخول أو يتربط تاني.';
  }

  @override
  String get devicesRevoked => 'اتلغى وصول الجهاز.';

  @override
  String get devicesRevokeFailed => 'مقدرناش نلغي وصول الجهاز ده. جرّب تاني.';

  @override
  String get settingsKnowledgeSubtitle =>
      'المستندات اللي Copilot يقدر يدوّر فيها';

  @override
  String get settingsMemorySubtitle => 'معلومات متصنّفة AURA فاكرها';

  @override
  String get settingsFeedbackSubtitle => 'التصحيحات اللي بعتّها';

  @override
  String get settingsEngineerTitle => 'المهندس';

  @override
  String get settingsEngineerSubtitle =>
      'مساحات الشغل، والـ PRs، وتشغيلات الـ Sandbox';

  @override
  String get settingsGithubTitle => 'ربط GitHub';

  @override
  String get settingsGithubSubtitle => 'اربط حساب GitHub بتاعك';

  @override
  String get settingsEngineStatusSubtitle => 'هل سيرفر AURA متاح ولا لأ';

  @override
  String get settingsAutomationsTitle => 'الأتمتة';

  @override
  String get settingsAutomationsSubtitle => 'مساعدين Gmail و GitHub و Drive';

  @override
  String get settingsUsageTitle => 'الاستخدام على الجهاز ده';

  @override
  String get settingsUsageSubtitle => 'زمن الاستجابة المحلي وعدد الطلبات';

  @override
  String get settingsDesktopIdeTitle => 'IDE الديسكتوب';

  @override
  String get settingsDesktopIdeOn => 'AURA بيفتح واجهة الـ IDE على الجهاز ده';

  @override
  String get settingsDesktopIdeOff =>
      'AURA بيفتح الشاشة الرئيسية العادية على الجهاز ده';

  @override
  String get settingsAboutTitle => 'AURA بيشتغل فين';

  @override
  String get settingsAboutBody =>
      'الطلبات بتتعالج على سيرفرات AURA، عمرها ما بتتعالج على الجهاز ده. مستنداتك وذكرياتك وملفاتك ونتايج الأدوات مبيقراهاش غير نماذج ذكاء اصطناعي AURA مشغّلها بنفسه. الاستثناء هو المهندس: بعد ما توافق في التطبيق، اللي بيقراه واللي بيشغّله بيروح لـ Claude، وده نموذج ذكاء اصطناعي بتشغّله Anthropic. لو مشغّل السيرفر فعّل الخاصية دي، رسايلك التانية ممكن يرد عليها مزوّد ذكاء اصطناعي خارجي معتمد.';

  @override
  String get themeSystem => 'زي الجهاز';

  @override
  String get themeLight => 'فاتح';

  @override
  String get themeDark => 'غامق';

  @override
  String get themeSystemSubtitle => 'نفس إعداد الجهاز';

  @override
  String get themeLightSubtitle => 'فاتح على طول';

  @override
  String get themeDarkSubtitle => 'غامق على طول';

  @override
  String get serverSwitchedSignInAgain => 'سجّل دخول تاني عشان تكمّل.';

  @override
  String get serverCurrent => 'السيرفر الحالي';

  @override
  String get serverReachable => 'متاح';

  @override
  String get serverUnreachable => 'مفيش اتصال';

  @override
  String get serverChecking => 'جاري التحقق…';

  @override
  String get serverReachabilityError => 'مقدرناش نتأكد من الاتصال';

  @override
  String get deleteAccountTitle => 'مسح الحساب';

  @override
  String get deleteAccountSubtitle => 'امسح حسابك وبياناته نهائيًا';

  @override
  String get deleteAccountConfirmTitle => 'عايز تمسح حسابك؟';

  @override
  String get deleteAccountConfirmBody =>
      'ده هيمسح حساب AURA بتاعك من السيرفر نهائيًا: الذاكرة، والمستندات، والمهام وباقي السجلات المتزامنة، والمحادثات، وكل جهاز مسجّل دخول. مفيش رجوع في الخطوة دي. بعدها AURA هيسجّل خروجك ويمسح بياناته من الجهاز ده. لو عايز تمشي من غير ما تمسح أي حاجة، سجّل خروج بس.';

  @override
  String get deleteAccountConfirm => 'مسح';

  @override
  String get deleteAccountDone => 'حسابك اتمسح.';

  @override
  String get deleteAccountPartial =>
      'جلساتك اتقفلت، بس جزء من حسابك لسه متمسحش. سجّل دخول واختار «مسح الحساب» تاني عشان تكمّل.';

  @override
  String get deleteAccountFailed => 'حسابك متمسحش.';

  @override
  String get serverSwitchConfirmTitle => 'عايز تغيّر السيرفر؟';

  @override
  String get serverSwitchConfirmBody =>
      'حسابك وبياناتك موجودين على السيرفر اللي إنت سايبه. AURA هيرفع التغييرات اللي لسه متزامنتش عليه الأول لو قدر، وبعدين يسجّل خروجك ويمسح بياناته من الجهاز ده. سجّل دخول تاني على السيرفر الجديد.';

  @override
  String get serverSwitchConfirm => 'تغيير';

  @override
  String get serverSwitchExplainer =>
      'تغيير السيرفر بيمسح جلستك على الجهاز ده. سجّل دخول تاني بعد ما تغيّر البيئة.';

  @override
  String get appName => 'AURA AI';

  @override
  String get cancel => 'إلغاء';

  @override
  String get signOut => 'تسجيل الخروج';

  @override
  String get signOutConfirmTitle => 'عايز تسجّل خروج؟';

  @override
  String get signOutConfirmBody =>
      'AURA بيرفع التغييرات اللي لسه متزامنتش الأول لو قدر يوصل للسيرفر، وبعدين يمسح بياناته من الجهاز ده. التغييرات اللي مقدرش يرفعها هتضيع.';

  @override
  String get loginBiometricReason =>
      'أكّد هويتك عشان تفتح بروفايل AURA AI بتاعك';

  @override
  String get loginTagline => 'نظام تشغيل شخصي بالذكاء الاصطناعي';

  @override
  String get loginWelcomeBack => 'أهلًا بيك تاني';

  @override
  String get loginSubtitle =>
      'سجّل دخول عشان تزامن مواعيدك ومذاكرتك ومساحة شغلك والـ Agents بتوعك.';

  @override
  String get loginSessionExpired =>
      'جلستك خلصت. سجّل دخول تاني عشان تكمّل من مكان ما وقفت — كل حاجة على الجهاز ده لسه موجودة.';

  @override
  String get loginSessionEndedPlanRequired =>
      'اتسجّل خروجك: باقتك مبقتش فيها الـEngineer على الكمبيوتر بتاعك. رقّي لـPro أو Team، وبعدين سجّل دخول تاني — كل حاجة على الجهاز ده لسه موجودة.';

  @override
  String get loginGoogle => 'تسجيل الدخول بـ Google';

  @override
  String get loginGoogleBrowser => 'تسجيل الدخول بـ Google (من المتصفح)';

  @override
  String get loginUnlockBiometrics => 'افتح بالبصمة';

  @override
  String get loginShowDeveloperPanel => 'دخول المطوّرين / من غير اتصال';

  @override
  String get loginHideDeveloperPanel => 'إخفاء لوحة المطوّرين';

  @override
  String get loginDeveloperName => 'اسم المطوّر';

  @override
  String get loginDeveloperNameHint => 'اكتب الاسم';

  @override
  String get loginDeveloperEmail => 'إيميل المطوّر';

  @override
  String get loginDeveloperEmailHint => 'اكتب الإيميل';

  @override
  String get loginDeveloperAccess => 'الدخول لبيئة المطوّرين';

  @override
  String get profileTitle => 'البروفايل والأمان';

  @override
  String get profileNotSignedIn => 'مش مسجّل دخول';

  @override
  String get profileShowAttestation => 'عرض حالة التحقق من الجهاز';

  @override
  String get add => 'إضافة';

  @override
  String get close => 'قفل';

  @override
  String get retry => 'جرّب تاني';

  @override
  String get moreOptions => 'المزيد';

  @override
  String get chatLoadEarlier => 'تحميل الرسايل الأقدم';

  @override
  String get captureTitle => 'اسأل Copilot';

  @override
  String get captureHint => 'سجّل فكرة، أو مهمة، أو سؤال…';

  @override
  String get captureSend => 'ابعت لـ Copilot';

  @override
  String get chatToggleKnowledge => 'المعرفة';

  @override
  String get chatToggleMemory => 'الذاكرة';

  @override
  String get chatToggleTools => 'الأدوات';

  @override
  String chatComposerWaiting(String persona) {
    return 'مستني $persona...';
  }

  @override
  String chatComposerHintShort(String persona) {
    return 'اسأل $persona...';
  }

  @override
  String chatComposerHint(String persona) {
    return 'اسأل $persona أي حاجة...';
  }

  @override
  String get chatStopGenerating => 'وقّف الرد';

  @override
  String get chatSend => 'إرسال';

  @override
  String get chatOpenSessions => 'جلسات الشات';

  @override
  String get chatLiveVoice => 'صوت مباشر';

  @override
  String get chatImageLimitReached => 'وصلت للحد الأقصى للصور';

  @override
  String get chatAttachImage => 'إرفاق صورة';

  @override
  String chatAttachedImageLabel(int index, int count, String name) {
    return 'صورة مرفقة $index من $count: $name';
  }

  @override
  String chatRemoveImage(int index) {
    return 'شيل الصورة $index';
  }

  @override
  String chatSentImageLabel(int index, int count, String name) {
    return 'صورة مبعوتة $index من $count: $name. افتح المعاينة';
  }

  @override
  String get chatClosePreview => 'قفل المعاينة';

  @override
  String get chatNewSession => 'جلسة جديدة';

  @override
  String get chatSessionsHeader => 'جلسات الشات';

  @override
  String get chatDefaultSession => 'مساحة الشغل الافتراضية';

  @override
  String chatSessionLabel(String id) {
    return 'جلسة $id';
  }

  @override
  String get chatDeleteConversation => 'مسح المحادثة';

  @override
  String get chatConversationDeleted => 'المحادثة اتمسحت';

  @override
  String get chatNoReplyTitle => 'مفيش رد المرة دي';

  @override
  String get chatNoReplyBody =>
      'AURA خلّص من غير ما يرد. دوس «جرّب تاني» عشان تبعت آخر رسالة تاني.';

  @override
  String get chatEmptyTitle => 'اسأل Copilot أي حاجة';

  @override
  String get chatEmptyMessage =>
      'أسئلة برمجة، أو أسئلة دراسية، أو تنظيم مهامك.';

  @override
  String get chatSuggestExplainTitle => 'اشرحلي فكرة';

  @override
  String get chatSuggestExplainSubtitle => 'اسأل سؤال في البرمجة أو الدراسة';

  @override
  String get chatSuggestExplainPrompt =>
      'اشرحلي الـ dependency injection بيشتغل إزاي';

  @override
  String get chatSuggestHealthTitle => 'سجّل صحتك';

  @override
  String get chatSuggestHealthSubtitle =>
      'سجّل المية اللي شربتها ومدة التمرين النهارده';

  @override
  String get chatSuggestHealthPrompt => 'سجّل 500ml مية و30 دقيقة تمرين';

  @override
  String get chatSuggestFinanceTitle => 'وضعك المالي';

  @override
  String get chatSuggestFinanceSubtitle => 'اعرض رصيدي الحالي وتوزيع مصاريفي';

  @override
  String get chatSuggestFinancePrompt => 'مصاريفي عاملة إيه الشهر ده؟';

  @override
  String get chatSuggestMemoryTitle => 'ذاكرة الـ Agent';

  @override
  String get chatSuggestMemorySubtitle =>
      'شوف المعلومات اللي النظام فاكرها عني';

  @override
  String get chatSuggestMemoryPrompt => 'إنت فاكر إيه عني؟';

  @override
  String get chatRateAnswer => 'قيّم الإجابة دي';

  @override
  String chatRatingStar(int value, int max) {
    return '$value من $max';
  }

  @override
  String get chatFeedbackCorrection => 'تصحيح (اختياري)';

  @override
  String get chatFeedbackSend => 'ابعت الملاحظات';

  @override
  String get chatFeedbackSent => 'شكرًا — الملاحظات اتبعتت.';

  @override
  String get memoryBankTitle => 'بنك الذاكرة';

  @override
  String get memoryBankExplainer =>
      'دي معلومات وتفضيلات AURA عرفها عنك. بتتضاف كـ system prompts عشان التفكير يبقى مخصّص ليك.';

  @override
  String get memoryBankAddHint => 'ضيف معلومة بنفسك...';

  @override
  String get memoryBankEmpty =>
      'مفيش ذكريات متسجّلة لسه. AURA بيستخرجها لوحده من المحادثات، أو تقدر تضيف واحدة من فوق.';

  @override
  String get memoryBankDelete => 'مسح الذكرى';

  @override
  String get chatCloudUnavailableTitle =>
      'الذكاء الاصطناعي السحابي مش متاح دلوقتي';

  @override
  String get chatCloudUnavailableBody =>
      'AURA مقدرش يوصل للمساعد السحابي. تقدر تجرّب تاني أو تبلّغ عن المشكلة.';

  @override
  String get chatReportIssue => 'بلّغ عن مشكلة';

  @override
  String get pendingActionsTitle => 'AURA بيقترح إجراءات';

  @override
  String get pendingActionsReject => 'رفض';

  @override
  String get pendingActionsApprove => 'موافقة على الإجراءات';

  @override
  String get pendingActionsAllSucceeded => 'الإجراءات المقترحة اتنفذت بنجاح!';

  @override
  String pendingActionsAllFailed(String labels) {
    return 'كل الإجراءات فشلت: $labels';
  }

  @override
  String pendingActionsSomeFailed(int count, String labels) {
    return 'إجراءات فشلت ($count): $labels';
  }

  @override
  String get voiceMicPermission => 'محتاجين إذن المايك عشان نسجّل رسالة صوتية.';

  @override
  String get voiceRecordingUnavailable => 'تسجيل الصوت مش متاح على الجهاز ده.';

  @override
  String get voiceTranscribeFailed =>
      'مقدرناش نحوّل الرسالة الصوتية دي لكلام مكتوب.';

  @override
  String get voiceReadFailed => 'مقدرناش نقرا التسجيل ده.';

  @override
  String get voiceStopRecording => 'وقّف التسجيل';

  @override
  String get voiceRecordNote => 'سجّل رسالة صوتية';

  @override
  String get voiceStopPlayback => 'إيقاف';

  @override
  String get voiceListen => 'اسمع الرد ده';

  @override
  String get engineerTaskHistory => 'سجل مهام المهندس';

  @override
  String get sandboxNoChanges => '(مفيش تغييرات)';

  @override
  String get stateToneError => 'مشكلة';

  @override
  String get stateToneWarning => 'تحذير';

  @override
  String get stateToneOffline => 'مفيش اتصال';

  @override
  String get commandPaletteLabel => 'لوحة الأوامر';

  @override
  String get commandPaletteHint => 'اكتب أمر...';

  @override
  String get commandPaletteNoMatchesTitle => 'مفيش أوامر مطابقة';

  @override
  String commandPaletteNoMatchesBody(String query) {
    return 'مفيش حاجة هنا مطابقة لـ «$query». جرّب كلمة أقصر، أو امسح البحث عشان تشوف كل حاجة.';
  }

  @override
  String get commandPaletteClearSearch => 'مسح البحث';

  @override
  String get refresh => 'تحديث';

  @override
  String get changeDate => 'تغيير التاريخ';

  @override
  String get clearDueDate => 'شيل ميعاد التسليم';

  @override
  String get taskMarkDone => 'علّم إنها خلصت';

  @override
  String get taskMarkNotDone => 'علّم إنها مخلصتش';

  @override
  String get hostdBrowseExecutable => 'دوّر على ملف تشغيل الـ Daemon';

  @override
  String get hostdSaveExecutable => 'حفظ مسار ملف التشغيل';

  @override
  String hostdPairedToOtherServer(String appBackendUrl) {
    return 'التطبيق ده مسجّل دخول على $appBackendUrl. اعمل Pair تاني عشان تنقل الـ daemon هناك.';
  }

  @override
  String get liveVoiceEnd => 'إنهاء الصوت المباشر';

  @override
  String get voiceLanguageLabel => 'لغة الكلام';

  @override
  String get voiceLiveArabicUnavailable =>
      'الصوت المباشر بالعربي مش متاح على السيرفر ده لسه، فالجلسة هتكمل بالإنجليزي.';

  @override
  String get eventChangeStart => 'تغيير وقت البداية';

  @override
  String get eventChangeEnd => 'تغيير وقت النهاية';

  @override
  String get eventAdd => 'إضافة ميعاد';

  @override
  String get eventDelete => 'مسح الميعاد';

  @override
  String get calendarPreviousMonth => 'الشهر اللي فات';

  @override
  String get calendarNextMonth => 'الشهر الجاي';

  @override
  String get githubImportSubtask => 'استيراد كمهمة برمجة';

  @override
  String get chatActivePersonaHeader => 'الشخصية الحالية';

  @override
  String get chatUseKnowledge => 'استخدام المعرفة (RAG)';

  @override
  String get chatRoutingHeader => 'توجيه الذكاء الاصطناعي';

  @override
  String get chatAiEngine => 'محرك الذكاء الاصطناعي';

  @override
  String get chatIssueRecorded => 'المشكلة اتسجّلت في التليمتري المحلي.';

  @override
  String chatPersonaFallback(String resolved, String requested) {
    return 'الطلب ده اتعامل معاه $resolved، مش $requested -- ممكن حسابك ميكونش ليه وصول لـ $requested.';
  }

  @override
  String get chatAwaitingConfirmation => 'أكّد الإجراء اللي تحت عشان تكمّل.';

  @override
  String get personaPickerTitle => 'اختار شخصية';

  @override
  String get loginDeveloperTip =>
      'نصيحة: تسجيل الدخول بـ Google فشل. عشان تجرّب محليًا من غير ما تسجّل بصمات SHA-1 في Google Console، استخدم اختيار التخطي «الدخول لبيئة المطوّرين» اللي فوق.';

  @override
  String planIncompleteHeadline(int total, int gaps) {
    String _temp0 = intl.Intl.pluralLogic(
      gaps,
      locale: localeName,
      other: 'AURA خطط لـ $total خطوات، و$gaps خطوة منهم ماخلصتش.',
      many: 'AURA خطط لـ $total خطوات، و$gaps خطوة منهم ماخلصتش.',
      few: 'AURA خطط لـ $total خطوات، و$gaps خطوات منهم ماخلصوش.',
      two: 'AURA خطط لـ $total خطوات، وخطوتين منهم ماخلصوش.',
      one: 'AURA خطط لـ $total خطوات، وخطوة منهم ماخلصتش.',
      zero: 'AURA خطط لـ $total خطوات وكلهم خلصوا.',
    );
    return '$_temp0';
  }

  @override
  String get planIncompleteAnnouncement => 'الإجابة دي اتقالت من غيرهم.';

  @override
  String planStepWithStatus(String step, String status) {
    return '$step — $status';
  }

  @override
  String get languageSectionTitle => 'اللغة';

  @override
  String get languageSystem => 'لغة الجهاز';

  @override
  String get languageSystemSubtitle => 'استخدم نفس لغة جهازك';

  @override
  String get deviceUnverifiedBanner =>
      'مقدرناش نتأكد من الجهاز ده. ممكن شوية مميزات متشتغلش.';

  @override
  String get commonDismiss => 'إخفاء';

  @override
  String get commonSave => 'حفظ';

  @override
  String get commonDelete => 'مسح';

  @override
  String get commonEdit => 'تعديل';

  @override
  String get commonAdd => 'إضافة';

  @override
  String get commonOk => 'تمام';

  @override
  String get commonConfirm => 'تأكيد';

  @override
  String get commonUndo => 'تراجع';

  @override
  String get commonBack => 'رجوع';

  @override
  String get commonNext => 'التالي';

  @override
  String get commonDone => 'خلاص';

  @override
  String get commonCopy => 'نسخ';

  @override
  String get commonCopied => 'اتنسخ';

  @override
  String get commonRefresh => 'تحديث';

  @override
  String get commonSearch => 'بحث';

  @override
  String get commonLoading => 'جاري التحميل…';

  @override
  String get commonSomethingWentWrong => 'حصلت مشكلة';

  @override
  String get commonNone => 'مفيش';

  @override
  String get commonYes => 'أيوه';

  @override
  String get commonNo => 'لأ';

  @override
  String get healthPageTitle => 'متابعة الصحة';

  @override
  String get healthPageSubtitle =>
      'تابع لياقتك، وسجّل المية والنوم، وراجع سجلاتك';

  @override
  String get healthDailyGoalsTitle => 'تقدّم أهداف النهارده';

  @override
  String healthWeightBadge(String weight) {
    return 'الوزن: $weight كجم';
  }

  @override
  String get healthWaterIntake => 'شرب المية';

  @override
  String get healthUnitMl => 'مل';

  @override
  String get healthSleepDuration => 'مدة النوم';

  @override
  String get healthUnitMins => 'دقيقة';

  @override
  String healthSleepHoursProgress(String hours) {
    return '$hours / 8.0 ساعة';
  }

  @override
  String get healthExerciseDuration => 'مدة التمرين';

  @override
  String get healthCaloriesConsumed => 'السعرات اللي اتاكلت';

  @override
  String get healthUnitKcal => 'سعرة';

  @override
  String get healthQuickLoggerTitle => 'تسجيل سريع';

  @override
  String get healthAddWater250 => '+250 مل مية';

  @override
  String get healthAddWater500 => '+500 مل مية';

  @override
  String get healthLogSleepTitle => 'سجّل مدة النوم';

  @override
  String get healthSleepMinutesLabel => 'دقايق النوم';

  @override
  String get healthSleepMinutesHint => 'مثلاً 480 يعني 8 ساعات';

  @override
  String get healthLogSleep => 'سجّل النوم';

  @override
  String get healthLogExerciseTitle => 'سجّل مدة التمرين';

  @override
  String get healthExerciseMinutesLabel => 'دقايق التمرين';

  @override
  String healthExampleHint(String example) {
    return 'مثلاً $example';
  }

  @override
  String get healthLogExercise => 'سجّل التمرين';

  @override
  String get healthLogCaloriesTitle => 'سجّل السعرات';

  @override
  String get healthKcalConsumedLabel => 'السعرات اللي اتاكلت';

  @override
  String get healthLogCalories => 'سجّل السعرات';

  @override
  String get healthLogWeightTitle => 'سجّل وزنك الحالي';

  @override
  String get healthWeightKgLabel => 'الوزن (كجم)';

  @override
  String get healthLogWeight => 'سجّل الوزن';

  @override
  String get healthWeeklyTrendsTitle => 'مؤشرات الصحة في الأسبوع';

  @override
  String get healthWeeklyTrendsEmpty =>
      'سجّل قياساتك عشان الرسوم الأسبوعية تتملي';

  @override
  String get healthWaterChartTitle => 'شرب المية (مل)';

  @override
  String get healthWeightChartTitle => 'تغيّر الوزن (كجم)';

  @override
  String get healthWeightChartEmpty => 'سجّل وزنك عشان يظهر خط التغيّر';

  @override
  String get knowledgeFileReadFailed => 'معرفناش نقرا الملف ده.';

  @override
  String get knowledgeAddDocument => 'إضافة مستند';

  @override
  String get knowledgeTitleLabel => 'العنوان';

  @override
  String get knowledgeContentLabel => 'المحتوى';

  @override
  String get knowledgeTitleContentRequired => 'لازم تكتب العنوان والمحتوى.';

  @override
  String get knowledgeUploading => 'جاري الرفع…';

  @override
  String get knowledgeUploadPdfInstead => 'ارفع PDF بدل كده';

  @override
  String get knowledgeSearchLabel => 'دوّر على جزء';

  @override
  String get knowledgeSearchEmpty => 'دوّرنا في مكتبتك — مفيش حاجة ليها علاقة.';

  @override
  String get knowledgeCagTitle => 'موجود في السياق على طول (CAG)';

  @override
  String knowledgeCagBudget(int tokens, int budget) {
    return 'الميزانية $tokens / $budget token';
  }

  @override
  String get knowledgeCagNothingPinned => 'مفيش حاجة متثبتة لسه';

  @override
  String get knowledgeCagHandbook => 'الدليل';

  @override
  String get knowledgeCagYours => 'بتاعك';

  @override
  String knowledgeCagDocTokens(String tokens) {
    return '$tokens token';
  }

  @override
  String get knowledgeLibrary => 'المكتبة';

  @override
  String get knowledgeLoadFailed => 'معرفناش نحمّل المستندات';

  @override
  String get knowledgeEmptyTitle => 'مفيش مستندات لسه';

  @override
  String get knowledgeEmptyMessage => 'احفظ عنوان وشوية كلام عشان تبني مكتبتك.';

  @override
  String get knowledgeAlwaysInContext => 'في السياق على طول';

  @override
  String get knowledgeUnpinTooltip => 'شيله من السياق الدائم';

  @override
  String get knowledgePinTooltip => 'خليه في السياق على طول';

  @override
  String get knowledgeAddFailed => 'معرفناش نضيف المستند ده.';

  @override
  String knowledgePassagesHeldBack(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count جزء اتحجزوا.',
      many: '$count جزء اتحجزوا.',
      few: '$count أجزاء اتحجزت.',
      two: 'جزأين اتحجزوا.',
      one: 'جزء واحد اتحجز.',
      zero: 'مفيش أجزاء اتحجزت.',
    );
    return '$_temp0';
  }

  @override
  String get knowledgeSaved => 'اتحفظ.';

  @override
  String get knowledgePdfUploadFailed => 'معرفناش نرفع الـ PDF ده.';

  @override
  String get knowledgeUploaded => 'اترفع.';

  @override
  String get goalsWelcomeBack => 'أهلاً بيك تاني،';

  @override
  String get goalsDefaultUserName => 'مستخدم Aura';

  @override
  String get goalsPageTitle => 'الأهداف';

  @override
  String get goalsPageSubtitle => 'تابع طموحاتك وتقدّمك';

  @override
  String get goalsNewGoal => 'هدف جديد';

  @override
  String get goalsEmptyTitle => 'مفيش أهداف لسه';

  @override
  String get goalsEmptyMessage => 'اعمل أول هدف ليك عشان تبدأ';

  @override
  String get goalsCreateGoalTitle => 'إنشاء هدف';

  @override
  String get goalsTitleRequiredLabel => 'العنوان *';

  @override
  String get goalsDescriptionLabel => 'الوصف';

  @override
  String get goalsStatusLabel => 'الحالة';

  @override
  String get goalsPriorityLabel => 'الأولوية';

  @override
  String get goalsSetTargetDate => 'حدد تاريخ الهدف (اختياري)';

  @override
  String goalsTargetDate(String date) {
    return 'الميعاد المستهدف: $date';
  }

  @override
  String goalsProgressPercent(int percent) {
    return 'التقدّم: $percent%';
  }

  @override
  String get goalsCreate => 'إنشاء';

  @override
  String get goalsStatusActive => 'شغّال';

  @override
  String get goalsStatusAchieved => 'اتحقق';

  @override
  String get goalsStatusAbandoned => 'اتساب';

  @override
  String get goalsPriorityLow => 'منخفضة';

  @override
  String get goalsPriorityMedium => 'متوسطة';

  @override
  String get goalsPriorityHigh => 'عالية';

  @override
  String get tasksWelcomeBack => 'أهلاً بيك تاني،';

  @override
  String get tasksDefaultUserName => 'مستخدم Aura';

  @override
  String get tasksPageTitle => 'مساحة المهام';

  @override
  String get tasksPageSubtitle => 'رتّب أولوياتك وظبّط أهدافك';

  @override
  String get tasksViewSyncLogs => 'عرض سجل المزامنة';

  @override
  String get tasksSyncing => 'جاري المزامنة...';

  @override
  String get tasksSyncFailed => 'حصلت مشاكل في مزامنة المهام.';

  @override
  String get tasksSyncSucceeded => 'مزامنة Google Tasks خلصت بنجاح!';

  @override
  String get tasksSyncButton => 'مزامنة المهام';

  @override
  String get tasksAddTask => 'إضافة مهمة';

  @override
  String get tasksAddNewTaskTitle => 'إضافة مهمة جديدة';

  @override
  String get tasksTitleLabel => 'عنوان المهمة';

  @override
  String get tasksTitleHint => 'محتاج تعمل إيه؟';

  @override
  String get tasksTitleRequired => 'اكتب عنوان لو سمحت';

  @override
  String get tasksDescriptionLabel => 'الوصف';

  @override
  String get tasksDescriptionHint => 'ضيف تفاصيل (اختياري)';

  @override
  String get tasksCategoryLabel => 'التصنيف';

  @override
  String get tasksPriorityLabel => 'الأولوية';

  @override
  String get tasksNoDueDate => 'مفيش ميعاد تسليم متحدد';

  @override
  String get tasksSaveTask => 'حفظ المهمة';

  @override
  String get tasksCategoryAll => 'الكل';

  @override
  String get tasksCategoryPersonal => 'شخصي';

  @override
  String get tasksCategoryAcademic => 'دراسي';

  @override
  String get tasksCategoryClient => 'عميل';

  @override
  String get tasksCategoryProject => 'مشروع';

  @override
  String get tasksPriorityLow => 'منخفضة';

  @override
  String get tasksPriorityMedium => 'متوسطة';

  @override
  String get tasksPriorityHigh => 'عالية';

  @override
  String get tasksEmptyTitle => 'خلّصت كل حاجة!';

  @override
  String get tasksEmptyMessage =>
      'استمتع بيومك أو اعمل مهمة جديدة عشان تفضل منظّم.';

  @override
  String get tasksSyncLogTitle => 'سجل مزامنة المهام';

  @override
  String get tasksSyncLogEmpty => 'مفيش سجلات لسه. دوس مزامنة عشان تبدأ.';

  @override
  String get tasksDeleteTask => 'مسح المهمة';

  @override
  String get tasksTaskDeleted => 'المهمة اتمسحت';

  @override
  String get calendarSyncFailed => 'حصلت مشاكل في مزامنة التقويم.';

  @override
  String get calendarSyncSucceeded => 'مزامنة Google Calendar خلصت بنجاح!';

  @override
  String get calendarPageTitle => 'التقويم';

  @override
  String get calendarPageSubtitle =>
      'نظّم مواعيدك على الجهاز واعمل مزامنة مع Google';

  @override
  String get calendarViewSyncLogs => 'عرض سجل المزامنة';

  @override
  String get calendarSyncing => 'جاري المزامنة...';

  @override
  String get calendarSyncButton => 'مزامنة التقويم';

  @override
  String get calendarSyncLogTitle => 'سجل المزامنة';

  @override
  String get calendarSyncLogEmpty => 'مفيش سجلات لسه. دوس مزامنة عشان تبدأ.';

  @override
  String get calendarCreateEventTitle => 'إنشاء ميعاد';

  @override
  String get calendarEventTitleLabel => 'عنوان الميعاد';

  @override
  String get calendarEventTitleHint => 'مثلاً اجتماع الفريق اليومي';

  @override
  String get calendarEventDescriptionLabel => 'الوصف (اختياري)';

  @override
  String get calendarEventDescriptionHint => 'اكتب تفاصيل الميعاد';

  @override
  String get calendarStartTime => 'وقت البداية';

  @override
  String get calendarEndTime => 'وقت النهاية';

  @override
  String get calendarEventCreated => 'الميعاد اتعمل بنجاح!';

  @override
  String get calendarCreate => 'إنشاء';

  @override
  String get calendarNoEvents => 'مفيش مواعيد النهارده';

  @override
  String get calendarCloudSynced => 'اتزامن مع السحابة';

  @override
  String get calendarDeleteEventTitle => 'مسح الميعاد';

  @override
  String calendarDeleteEventConfirm(String title) {
    return 'متأكد إنك عايز تمسح «$title»؟';
  }

  @override
  String get calendarEventDeleted => 'الميعاد اتمسح.';

  @override
  String get hostPanelTitle => 'الـ Host Daemon';

  @override
  String get hostFullSettings => 'كل الإعدادات';

  @override
  String hostStatusError(String error) {
    return 'مشكلة: $error';
  }

  @override
  String get hostStatusGaveUp => 'وقف بعد ما وقع كذا مرة';

  @override
  String hostStatusRestarting(int seconds) {
    return 'وقع، وهيشتغل تاني بعد $seconds ثانية...';
  }

  @override
  String get hostStatusNotRunning => 'مش شغّال';

  @override
  String get hostStatusRunningNotPaired => 'شغّال، بس مش متربط';

  @override
  String get hostStatusRunningOtherServer => 'شغّال، ومتربط بسيرفر تاني';

  @override
  String get hostStatusRunningPaired => 'شغّال ومتربط';

  @override
  String get hostStatusReconnecting => 'شغّال، وبيحاول يتصل تاني...';

  @override
  String get hostStatusPairedUpdateDaemon => 'شغّال ومتربط (حدّث aura_hostd)';

  @override
  String hostBackendLabel(String url) {
    return 'السيرفر: $url';
  }

  @override
  String hostOtherServerNotice(String url) {
    return 'التطبيق ده مسجّل دخول على $url. اربطه تاني عشان تنقل الـ daemon هناك.';
  }

  @override
  String get hostStarting => 'بيشتغل...';

  @override
  String get hostStartNow => 'شغّل دلوقتي';

  @override
  String get hostRequiredNotRunning =>
      'الـ Host daemon مش شغّال. شغّله علشان AURA يقدر يوصل لملفات الـ workspace ده.';

  @override
  String get hostRequiredNotPaired =>
      'الـ Host daemon شغّال بس مش متربط بحسابك. اربطه علشان تكمّل.';

  @override
  String get agentHostNotRunning =>
      'الـ Host daemon مش شغّال. الـ Engineer مش هيقدر يقرا أو يعدّل الملفات لحد ما يشتغل.';

  @override
  String get agentHostNotPaired =>
      'الـ Host daemon مش متربط. الـ Engineer مش هيقدر يقرا أو يعدّل الملفات لحد ما تربطه.';

  @override
  String get hostStart => 'تشغيل';

  @override
  String get hostCancelRestart => 'إلغاء إعادة التشغيل';

  @override
  String get hostStop => 'إيقاف';

  @override
  String get hostPairing => 'جاري الربط...';

  @override
  String get hostRepair => 'ربط تاني';

  @override
  String get hostPair => 'ربط';

  @override
  String get hostGrantedRoots => 'الفولدرات المسموحة';

  @override
  String get hostTrusted => 'موثوق';

  @override
  String get hostSetExecutableFirst =>
      'حدد مسار ملف تشغيل aura_hostd من «كل الإعدادات» الأول.';

  @override
  String hostCouldNotStart(String error) {
    return 'مقدرناش نشغّل aura_hostd: $error';
  }

  @override
  String hostCouldNotStop(String error) {
    return 'مقدرناش نوقف aura_hostd: $error';
  }

  @override
  String get hostPairDialogTitle => 'ربط الجهاز ده';

  @override
  String get hostPairApproving =>
      'بنوافق بجلسة AURA اللي انت مسجّل دخول بيها...';

  @override
  String get hostPairWaiting => 'مستنيين الموافقة...';

  @override
  String get hostPairSignInFirst =>
      'سجّل دخول على AURA الأول، الربط محتاج جلسة شغّالة عشان يتوافق عليه.';

  @override
  String get hostPaired => 'اتربط.';

  @override
  String hostPairingFailed(String reason) {
    return 'الربط فشل: $reason';
  }

  @override
  String get hostPairingExpired => 'كود الربط خلص وقته.';

  @override
  String hostCouldNotStartPairing(String error) {
    return 'مقدرناش نبدأ الربط: $error';
  }

  @override
  String get debugOpenWorkspace =>
      'افتح مساحة شغل عشان تستخدم الـ Debug Console.';

  @override
  String get debugScriptHint => 'script.py (نسبةً لفولدر مساحة الشغل الرئيسي)';

  @override
  String get debugStart => 'ابدأ';

  @override
  String get debugContinue => 'كمّل';

  @override
  String get debugStepOver => 'Step Over';

  @override
  String get debugStepInto => 'Step Into';

  @override
  String get debugStepOut => 'Step Out';

  @override
  String get debugStop => 'إيقاف';

  @override
  String get debugStatusIdle => 'مستني';

  @override
  String get debugStatusStarting => 'بيبدأ...';

  @override
  String debugStatusRunningPid(int pid) {
    return 'شغّال (pid $pid)';
  }

  @override
  String get debugStatusRunning => 'شغّال';

  @override
  String get debugStatusPaused => 'متوقف مؤقتًا';

  @override
  String get debugStatusStopped => 'وقف';

  @override
  String get debugBreakpointsLabel => 'الـ Breakpoints:';

  @override
  String get debugLineHint => 'رقم السطر';

  @override
  String get debugCallStack => 'الـ CALL STACK';

  @override
  String get debugVariables => 'المتغيرات';

  @override
  String get controlCenterDiscardTitle => 'عايز تمسح المهمة دي؟';

  @override
  String controlCenterDiscardBody(String branch, String path) {
    return 'ده هيمسح الـ Branch \"$branch\" والـ worktree بتاعه في $path نهائي. أي شغل فيه متعملوش Merge هيضيع، ومش هتقدر ترجّعه.';
  }

  @override
  String get controlCenterDiscard => 'مسح';

  @override
  String get controlCenterMergedTitle => 'اتعمل Merge';

  @override
  String controlCenterMergedBody(String branch) {
    return 'اتعمل Merge لـ \"$branch\" على الجهاز. تمسح الـ worktree بتاعه؟';
  }

  @override
  String get controlCenterKeepIt => 'سيبه';

  @override
  String get controlCenterTitle => 'مراجعة الـ Agent';

  @override
  String get controlCenterEmpty =>
      'مفيش مهام للمهندس تراجعها لسه. كل شات مع المهندس بيشتغل في نسخة لوحده من الفولدر، على Branch اسمه aura/task-…، وبيظهر هنا علشان تراجعه وتعمل له Merge.';

  @override
  String get controlCenterNoChanges => '(مفيش تغييرات)';

  @override
  String controlCenterDiffStatError(String message) {
    return 'مشكلة: $message';
  }

  @override
  String get controlCenterBaseBranchUnknown =>
      'مقدرناش نعرف الـ Branch الأساسي اللي نقارن بيه.';

  @override
  String get controlCenterMergeConflict =>
      'فيه Merge conflict -- حلّه في الـ worktree الأساسي، وبعدين اعمل Commit هناك على طول.';

  @override
  String get controlCenterHideDiff => 'إخفاء الـ diff';

  @override
  String get controlCenterViewDiff => 'عرض الـ diff';

  @override
  String get controlCenterSquashMerge => 'Squash merge';

  @override
  String get controlCenterWorking => 'شغّالين...';

  @override
  String get controlCenterMerge => 'Merge';

  @override
  String get terminalOpenWorkspace => 'افتح مساحة شغل عشان تستخدم التيرمينال.';

  @override
  String get terminalGrantWorkspace =>
      'اسمح للـ Host Daemon بمساحة الشغل دي عشان تستخدم التيرمينال.';

  @override
  String get terminalUntrusted =>
      'مساحة الشغل دي مش موثوقة. خليها موثوقة من لوحة الـ Host عشان تستخدم التيرمينال.';

  @override
  String get terminalStarting => 'التيرمينال بيشتغل...';

  @override
  String terminalSessionEndedWithCode(int code) {
    return 'الجلسة خلصت (exit code $code).';
  }

  @override
  String get terminalSessionEnded => 'الجلسة خلصت.';

  @override
  String get terminalRestart => 'إعادة تشغيل';

  @override
  String get outputFilterAll => 'الكل';

  @override
  String get outputChannelAgent => 'الـ Agent';

  @override
  String get outputChannelHost => 'الـ Host';

  @override
  String get outputChannelTasks => 'المهام';

  @override
  String get outputChannelDiagnostics => 'التشخيصات';

  @override
  String get outputClear => 'مسح الـ Output';

  @override
  String get outputEmpty => 'مفيش Output لسه';

  @override
  String get diagnosticsOpenWorkspace => 'افتح مساحة شغل عشان تشوف المشاكل.';

  @override
  String get diagnosticsDetecting => 'بندوّر على أداة التشخيص...';

  @override
  String get diagnosticsNoTool => 'ملقيناش أداة تشخيص لمساحة الشغل دي.';

  @override
  String get diagnosticsRerun => 'شغّل تاني';

  @override
  String get diagnosticsNoProblems => 'مفيش مشاكل';

  @override
  String diagnosticsToolNotFound(String tool) {
    return '\"$tool\" مش متسطّب أو مش موجود في الـ PATH.';
  }

  @override
  String get editorNoFileOpen => 'مفيش ملف مفتوح';

  @override
  String get editorNoFileOpenHint => 'افتح ملف من المستكشف أو دوس Ctrl+P';

  @override
  String editorCouldNotRead(String path, String error) {
    return 'مقدرناش نقرا $path: $error';
  }

  @override
  String get editorNoActiveWorkspace => 'مفيش مساحة شغل مفتوحة';

  @override
  String get editorRestoredNotice =>
      'رجّعنالك التغييرات اللي متحفظتش من آخر جلسة.';

  @override
  String get editorRestoredOverChange =>
      'رجّعنالك التغييرات اللي متحفظتش من آخر جلسة. الملف اتغيّر على الجهاز من ساعتها، ولو حفظت هتكتب فوق التغيير ده.';

  @override
  String get editorUnsavedTitle => 'تغييرات متحفظتش';

  @override
  String editorUnsavedBody(String title) {
    return 'فيه تغييرات متحفظتش في \"$title\".';
  }

  @override
  String get editorDiscard => 'تجاهل';

  @override
  String get editorDiskChangedTitle => 'الملف اتغيّر على الجهاز';

  @override
  String editorDiskChangedBody(String title) {
    return '\"$title\" اتغيّر برّه المحرر من ساعة ما فتحته. لو حفظت دلوقتي هتكتب فوق التغييرات دي.';
  }

  @override
  String get editorOverwrite => 'اكتب فوقه';

  @override
  String get editorReloadFromDisk => 'حمّله تاني من الجهاز';

  @override
  String editorSaveFailed(String title, String error) {
    return 'مقدرناش نحفظ $title: $error';
  }

  @override
  String get editorNotEditable => 'الملف ده مينفعش يتعدّل لأنه معرفناش نفتحه.';

  @override
  String get editorQuickOpenHint => 'اكتب عشان تدوّر على ملفات...';

  @override
  String get editorNoFilesFound => 'مفيش ملفات';

  @override
  String get editorNoMatches => 'مفيش نتايج';

  @override
  String agentWorkspaceLabel(String path) {
    return 'مساحة الشغل: $path';
  }

  @override
  String get agentLockedTitle => 'المهندس مش متاح على الحساب ده';

  @override
  String get agentLockedMessage =>
      'الـEngineer مش جزء من مساحة العمل دي. بييجي مع باقتي Pro وTeam، أو مشغّل السيرفر يقدر يدّيه لدورك. لحد كده، الرسايل من هنا هيرد عليها المساعد العام بدل منه.';

  @override
  String get agentLockedElsewhereTitle =>
      'الـEngineer موجود في مساحة عمل تانية';

  @override
  String agentLockedElsewhereMessage(String workspace) {
    return 'باقة مساحة العمل دي مفيهاش الـEngineer، لكن $workspace فيها. انقل لها عشان تستخدم الـEngineer هنا.';
  }

  @override
  String agentLockedSwitchTo(String workspace) {
    return 'انقل لـ$workspace';
  }

  @override
  String get agentEmptyTitle => 'مفيش رسايل لسه';

  @override
  String get agentEmptyMessage =>
      'اسأل المهندس عن مساحة الشغل دي — يقدر يقرا ملفاتك، ويشغّل أوامر، ويقترح تغييرات تراجعها قبل ما أي حاجة تتطبّق.';

  @override
  String get agentTaskDefault => 'الافتراضية';

  @override
  String agentTaskLabel(String id) {
    return 'مهمة $id';
  }

  @override
  String get agentSwitchTask => 'تغيير المهمة';

  @override
  String get agentNewTask => '+ مهمة جديدة';

  @override
  String get engineeringDaemonSaved => 'اتحفظ.';

  @override
  String get engineeringDaemonPathMissing =>
      'حدد مسار ملف تشغيل aura_hostd الأول.';

  @override
  String engineeringDaemonStartFailed(String error) {
    return 'مقدرناش نشغّل aura_hostd: $error';
  }

  @override
  String engineeringDaemonStopFailed(String error) {
    return 'مقدرناش نوقف aura_hostd: $error';
  }

  @override
  String get engineeringPairThisDevice => 'اربط الجهاز ده';

  @override
  String get engineeringPairApproving =>
      'بنوافق باستخدام جلسة AURA اللي انت داخل بيها...';

  @override
  String get engineeringPairWaiting => 'مستنيين الموافقة...';

  @override
  String get engineeringPairSignInFirst =>
      'سجّل دخولك في AURA الأول -- الربط محتاج جلسة شغّالة عشان يوافق بيها.';

  @override
  String engineeringPairApprovalRejected(int status, String body) {
    return 'الموافقة اترفضت: HTTP $status $body';
  }

  @override
  String get engineeringPaired => 'اتربط.';

  @override
  String engineeringPairedNamed(String name) {
    return 'اتربط $name.';
  }

  @override
  String engineeringPairingFailed(String reason) {
    return 'الربط فشل: $reason';
  }

  @override
  String get engineeringPairingExpired => 'كود الربط خلص وقته.';

  @override
  String engineeringPairingStartFailed(String error) {
    return 'مقدرناش نبدأ الربط: $error';
  }

  @override
  String engineeringGrantRootFailed(String path, String error) {
    return 'مقدرناش نسمح بـ $path: $error';
  }

  @override
  String get engineeringRevokeRootTitle => 'عايز تشيل صلاحية الفولدر ده؟';

  @override
  String engineeringRevokeRootBody(String path) {
    return 'الـ agent مش هيقدر يقرا أو يكتب أي حاجة جوّه $path تاني.';
  }

  @override
  String get engineeringRevoke => 'شيل الصلاحية';

  @override
  String get engineeringDaemonTitle =>
      'الـ Host Daemon بتاع المهندس على الكمبيوتر';

  @override
  String engineeringDaemonStatusFailed(String error) {
    return 'مقدرناش نشوف الحالة: $error';
  }

  @override
  String get engineeringExecutableTitle => 'ملف تشغيل aura_hostd';

  @override
  String get engineeringExecutableHelp =>
      'النسخ المتسطّبة بتستخدم aura_hostd اللي جاي مع الأبلكيشن. لو عايز تستخدم واحد تاني، حط مساره هنا (شوف aura_hostd/README.md)؛ وامسحه عشان ترجع للي جاي مع الأبلكيشن.';

  @override
  String get engineeringDaemonNotRunning => 'مش شغّال';

  @override
  String get engineeringDaemonRunningNotPaired => 'شغّال، بس مش مربوط';

  @override
  String get engineeringDaemonRunningOtherServer => 'شغّال، ومربوط بسيرفر تاني';

  @override
  String get engineeringDaemonRunningPaired => 'شغّال ومربوط';

  @override
  String get engineeringDaemonReconnecting => 'شغّال، وبيحاول يتصل تاني...';

  @override
  String get engineeringDaemonPairedUpdateNeeded =>
      'شغّال ومربوط -- حدّث aura_hostd عشان تشوف حالة الاتصال';

  @override
  String engineeringDaemonBackend(String url) {
    return 'السيرفر: $url';
  }

  @override
  String engineeringDaemonOtherServerNote(String url) {
    return 'الأبلكيشن ده داخل على $url. اربط تاني عشان تنقل الـ daemon هناك.';
  }

  @override
  String get engineeringDaemonStart => 'تشغيل';

  @override
  String get engineeringDaemonStop => 'إيقاف';

  @override
  String get engineeringPairAgain => 'اربط تاني';

  @override
  String get engineeringWorkspaceRoots => 'فولدرات مساحة الشغل';

  @override
  String get engineeringNoRoots =>
      'لسه مفيش فولدرات متاحة -- الـ agent مش هيقدر يقرا أو يكتب أي حاجة على الجهاز ده لحد ما تضيف واحد.';

  @override
  String get engineeringRootTrusted =>
      'موثوق -- أوامر الـ agent بتشتغل من غير ما تسألك، باسمك، وتقدر توصل لأي حاجة حسابك يقدر يوصلها. المسارات اللي برّه الفولدر ده وأي git push بيسألوك الأول.';

  @override
  String get engineeringRootUntrusted =>
      'مش موثوق -- أوامر القراية بس هي اللي بتشتغل؛ أي حاجة تانية بتسألك الأول';

  @override
  String get engineeringBrowse => 'تصفّح';

  @override
  String get engineeringDaemonOutput => 'مخرجات الـ daemon';

  @override
  String get engineeringNoLogsNote =>
      'المخرجات المباشرة بتظهر بس لـ daemon الأبلكيشن ده هو اللي شغّله -- لو بتستخدم واحد شغّال من مكان تاني، كل حاجة تانية في الصفحة دي هتشتغل عادي.';

  @override
  String get engineeringBackToFileList => 'ارجع لقايمة الملفات';

  @override
  String get engineeringFileTruncated =>
      'الملف أكبر من اللي ظاهر في المعاينة دي.';

  @override
  String engineeringReadDirFailed(String error) {
    return 'مقدرناش نقرا الفولدر ده: $error';
  }

  @override
  String get engineeringFolderEmpty => 'الفولدر ده فاضي.';

  @override
  String get engineeringGithubBrowserFailed =>
      'مقدرناش نفتح المتصفح عشان نربط GitHub.';

  @override
  String get engineeringGithubDisconnected => 'اتفصل GitHub.';

  @override
  String get engineeringGithubConnectedUnknown => 'متوصّل بحساب GitHub';

  @override
  String engineeringGithubConnectedAs(String login) {
    return 'متوصّل باسم $login';
  }

  @override
  String get engineeringGithubNotConnected => 'مفيش حساب GitHub متوصّل';

  @override
  String engineeringGithubConnectedAt(String date) {
    return 'اتوصّل $date';
  }

  @override
  String get engineeringGithubConnectedBody =>
      'المهندس يقدر يقرا ويكتب في الريبوهات اللي انت سمحت بيها بالظبط لما سطّبت الـ GitHub App. الفصل بيشيل بس اللي AURA تقدر تستخدمه -- مش بيلغي الأبلكيشن على github.com.';

  @override
  String get engineeringGithubDisconnectedBody =>
      'اربط حساب GitHub بتاعك عشان المهندس (يفتح PRs، يقرا الـ issues، يعمل Commit للملفات) يشتغل على الريبوهات اللي انت تختارها، بدل ما يعتمد على token مشترك حد من الإدارة ظبطه.';

  @override
  String get engineeringGithubDisconnect => 'افصل GitHub';

  @override
  String get engineeringGithubConnect => 'اربط GitHub';

  @override
  String get engineeringDesktopDaemonTooltip => 'الـ daemon بتاع الكمبيوتر';

  @override
  String get engineeringNoRunsTitle => 'لسه المهندس ماعملش أي حاجة';

  @override
  String get engineeringNoRunsMessage =>
      'اطلب من المهندس يفتح PR أو يشغّل أمر، وهيظهر هنا.';

  @override
  String get engineeringToolOpenedDraftPr => 'فتح PR مسودة';

  @override
  String get engineeringToolCommittedFiles => 'عمل Commit لملفات';

  @override
  String get engineeringToolCreatedBranch => 'عمل Branch جديد';

  @override
  String get engineeringToolWroteFile => 'كتب ملف';

  @override
  String get engineeringToolEditedFile => 'عدّل ملف';

  @override
  String get engineeringToolRanCommand => 'شغّل أمر';

  @override
  String get engineeringToolCommitted => 'عمل Commit';

  @override
  String get engineeringToolSwitchedBranch => 'غيّر الـ Branch';

  @override
  String get engineeringToolDiffedSandbox => 'عمل Diff للـ sandbox';

  @override
  String get engineeringRunStatusPending => 'مستني';

  @override
  String get engineeringRunStatusSucceeded => 'نجح';

  @override
  String get engineeringRunStatusFailed => 'فشل';

  @override
  String engineeringRunStatus(String status) {
    return 'الحالة: $status';
  }

  @override
  String engineeringRunBranch(String branch) {
    return 'الـ Branch: $branch';
  }

  @override
  String engineeringRunCommit(String sha) {
    return 'الـ Commit: $sha';
  }

  @override
  String get engineeringCopyLink => 'انسخ اللينك';

  @override
  String get engineeringLinkCopied => 'اتنسخ اللينك';

  @override
  String get engineeringRunOutput => 'المخرجات';

  @override
  String get automationsTitle => 'أتمتة AURA';

  @override
  String get automationsSubtitle =>
      'طلّع مهام من الإيميلات والـ issues، واعمل هيكل لمساحات الشغل';

  @override
  String get automationsTabGmail => 'تحليل إيميلات Gmail';

  @override
  String get automationsTabGithub => 'استيراد issues من GitHub';

  @override
  String get automationsTabDrive => 'بناء مساحة شغل على Drive';

  @override
  String get automationsGmailUnavailableTitle => 'ربط Gmail مش متاح';

  @override
  String get automationsGmailUnavailableBody =>
      'انت داخل بحساب مطوّر تجريبي. الإيميلات اللي ماتقرتش الحقيقية بتتجاب بس لما تدخل بحساب Google حقيقي.';

  @override
  String get automationsGmailRecentUnread => 'آخر الإيميلات اللي ماتقرتش';

  @override
  String get automationsGmailFetchUnread => 'هات اللي ماتقراش';

  @override
  String get automationsGmailFetching => 'بنجيب...';

  @override
  String get automationsGmailEmpty =>
      'مفيش إيميلات ماتقرتش اتجابت. دوس على الزرار عشان تجيبها.';

  @override
  String automationsGmailFrom(String sender) {
    return 'من: $sender';
  }

  @override
  String get automationsGmailAnalyze => 'حلّل بالـ AI';

  @override
  String automationsGmailCategory(String category) {
    return 'التصنيف: $category';
  }

  @override
  String get automationsGmailUrgent => 'مستعجل';

  @override
  String get automationsGmailLowUrgency => 'مش مستعجل';

  @override
  String get automationsGmailAiSummary => 'ملخص الـ AI';

  @override
  String get automationsGmailActionItems => 'المهام اللي اتطلّعت';

  @override
  String automationsGmailSuggestedDeadline(String date) {
    return 'آخر ميعاد مقترح: $date';
  }

  @override
  String get automationsGmailImportTask => 'ضيفها كمهمة في AURA';

  @override
  String automationsGmailImportedTask(String title) {
    return 'اتضافت المهمة: $title';
  }

  @override
  String get automationsGmailSuggestedEvent => 'ميعاد مقترح للتقويم';

  @override
  String get automationsGmailScheduleEvent => 'ضيف الميعاد';

  @override
  String automationsGmailScheduledEvent(String title) {
    return 'اتضاف الميعاد: $title';
  }

  @override
  String automationsGmailFetchError(String error) {
    return 'مشكلة في جلب إيميلات Gmail: $error';
  }

  @override
  String automationsGmailAnalysisFailed(String error) {
    return 'التحليل فشل: $error';
  }

  @override
  String get automationsGmailNoSubject => 'من غير عنوان';

  @override
  String get automationsGmailUnknownSender => 'راسل مش معروف';

  @override
  String get automationsGithubSetup => 'إعداد ربط GitHub';

  @override
  String get automationsGithubPatLabel => 'GitHub PAT (الـ token الشخصي)';

  @override
  String get automationsGithubRepoLabel => 'مسار الريبو';

  @override
  String get automationsGithubLoadIssues => 'هات الـ issues بتاعة الريبو';

  @override
  String get automationsGithubLoading => 'جاري تحميل الـ issues من GitHub...';

  @override
  String get automationsGithubEmpty => 'مفيش issues مفتوحة اتجابت.';

  @override
  String get automationsGithubExtractSubtasks => 'طلّع المهام الفرعية';

  @override
  String automationsGithubComplexity(String complexity) {
    return 'الصعوبة: $complexity';
  }

  @override
  String automationsGithubPriority(String priority) {
    return 'الأولوية: $priority';
  }

  @override
  String get automationsGithubIssueSummary => 'ملخص الـ issue';

  @override
  String get automationsGithubCodingTasks => 'مهام البرمجة اللي اتطلّعت';

  @override
  String get automationsGithubImported => 'اتضافت مهمة البرمجة!';

  @override
  String get automationsGithubMissingInput =>
      'لازم تكتب الـ GitHub PAT ومسار الريبو الاتنين.';

  @override
  String get automationsGithubRepoFormat =>
      'مسار الريبو لازم يكون بالشكل ده \'owner/repo\' (زي owner/AURA-AI).';

  @override
  String automationsGithubFetchFailed(String error) {
    return 'جلب البيانات من GitHub فشل: $error';
  }

  @override
  String automationsGithubAnalysisFailed(String error) {
    return 'تحليل الـ issue فشل: $error';
  }

  @override
  String get automationsDriveUnavailableTitle =>
      'إنشاء مساحة شغل على Google Drive مش متاح';

  @override
  String get automationsDriveUnavailableBody =>
      'انت داخل بحساب مطوّر تجريبي. فولدرات مساحة الشغل على Drive بتتعمل بس لما تدخل بحساب Google حقيقي.';

  @override
  String get automationsDriveGeneratorTitle =>
      'إنشاء مساحة شغل لمشروع على Drive';

  @override
  String get automationsDriveGeneratorBody =>
      'اكتب تفاصيل المشروع. AURA هتعمل فولدر على Google Drive وتحط فيه مستند Markdown فيه خطة المشروع مترتبة.';

  @override
  String get automationsDriveProjectName => 'اسم المشروع';

  @override
  String get automationsDriveProjectNameHint => 'مثلاً: أبلكيشن موبايل فريلانس';

  @override
  String get automationsDriveProjectDescription =>
      'وصف المشروع / الخطوط العريضة';

  @override
  String get automationsDriveProjectDescriptionHint =>
      'اوصف التفاصيل عشان نعمل خطة جاهزة';

  @override
  String get automationsDriveGenerate => 'اعمل مساحة الشغل على Drive';

  @override
  String get automationsDriveConsole => 'سجل خطوات الأتمتة';

  @override
  String get automationsDriveInitializing =>
      'بنجهّز فولدرات مساحة شغل المشروع...';

  @override
  String automationsDriveFailed(String error) {
    return 'إنشاء مساحة الشغل فشل: $error';
  }

  @override
  String get automationsDriveRequestingPermissions =>
      'بنطلب صلاحيات التخزين على Drive...';

  @override
  String get automationsDriveConnected => 'اتصلنا بـ Google Drive.';

  @override
  String automationsDriveCreatedFolder(String name) {
    return 'اتعمل الفولدر: $name';
  }

  @override
  String automationsDriveCreatedDocument(String name) {
    return 'اتعمل المستند: $name';
  }

  @override
  String get automationsDriveDone => 'مساحة الشغل على Drive اتعملت بنجاح!';

  @override
  String get financeTitle => 'دفتر الماليات';

  @override
  String get financeSubtitle => 'تابع الدخل والمصاريف، وحلّل فلوسك بتروح فين';

  @override
  String get financeBalance => 'الرصيد';

  @override
  String get financeIncome => 'الدخل';

  @override
  String get financeExpenses => 'المصاريف';

  @override
  String get financeExpensesByCategory => 'المصاريف حسب التصنيف';

  @override
  String get financeNoExpenses => 'مفيش مصاريف متسجلة';

  @override
  String get financeIncomeVsExpense => 'الدخل قصاد المصاريف';

  @override
  String get financeExpense => 'مصروف';

  @override
  String get financeLogTransaction => 'سجّل معاملة';

  @override
  String financeAmountLabel(String currency) {
    return 'المبلغ ($currency)';
  }

  @override
  String get financeCategory => 'التصنيف';

  @override
  String get financeCategoryFood => 'أكل';

  @override
  String get financeCategoryStudies => 'دراسة';

  @override
  String get financeCategoryFreelance => 'فريلانس';

  @override
  String get financeCategoryHousing => 'سكن';

  @override
  String get financeCategoryEntertainment => 'ترفيه';

  @override
  String get financeCategoryUtilities => 'فواتير';

  @override
  String get financeCategoryTransport => 'مواصلات';

  @override
  String get financeCategoryOther => 'حاجات تانية';

  @override
  String get financeDescriptionLabel => 'الوصف (اختياري)';

  @override
  String get financeDescriptionHint => 'مثلاً: طلبات البيت بتاعة الأسبوع';

  @override
  String get financeDate => 'التاريخ';

  @override
  String get financeLogged => 'المعاملة اتسجلت!';

  @override
  String get financeSubmit => 'سجّل في الدفتر';

  @override
  String get financeInvalidAmount => 'اكتب مبلغ صحيح أكبر من صفر.';

  @override
  String get financeChooseCategory => 'اختار تصنيف.';

  @override
  String get financeRecentActivities => 'آخر المعاملات';

  @override
  String get financeNoActivities => 'لسه مفيش معاملات متسجلة';

  @override
  String financeDeletedEntry(String entry) {
    return 'اتمسحت المعاملة: $entry';
  }

  @override
  String get ideClosePanel => 'اقفل اللوحة';

  @override
  String get ideActivityExplorer => 'المستكشف';

  @override
  String get ideActivityExplorerTooltip => 'المستكشف (Ctrl+B)';

  @override
  String get ideActivityGoToFile => 'روح لملف';

  @override
  String get ideActivityGoToFileTooltip => 'روح لملف (Ctrl+P)';

  @override
  String get ideActivitySourceControl => 'التحكم في الإصدارات';

  @override
  String get ideActivityAgents => 'وكلاء AURA';

  @override
  String get ideActivityAgentsTooltip => 'وكلاء AURA (Ctrl+J)';

  @override
  String get ideActivityControlCenterTooltip => 'مركز تحكم AURA';

  @override
  String get ideActivityPanel => 'اللوحة';

  @override
  String get ideActivityPanelTooltip =>
      'التيرمينال والمخرجات والمشاكل (Ctrl+Shift+J)';

  @override
  String ideActivityPanelProblems(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count مشكلة',
      many: '$count مشكلة',
      few: '$count مشاكل',
      two: 'مشكلتين',
      one: 'مشكلة واحدة',
      zero: 'مفيش مشاكل',
    );
    return 'اللوحة، $_temp0';
  }

  @override
  String get ideBottomTerminal => 'التيرمينال';

  @override
  String get ideBottomOutput => 'المخرجات';

  @override
  String ideBottomProblemsCount(int count) {
    return 'المشاكل ($count)';
  }

  @override
  String get ideBottomProblems => 'المشاكل';

  @override
  String get ideBottomDebugConsole => 'كونسول الـ Debug';

  @override
  String get ideCmdNoWorkspace => 'مفيش مساحة شغل مفتوحة';

  @override
  String get ideCmdNoFileOpen => 'مفيش ملف مفتوح';

  @override
  String get ideCmdGroupFile => 'ملف';

  @override
  String get ideCmdGroupView => 'العرض';

  @override
  String get ideCmdGoToFile => 'روح لملف...';

  @override
  String get ideCmdSaveFile => 'حفظ الملف';

  @override
  String get ideCmdNoUnsavedChanges => 'مفيش تغييرات مش محفوظة';

  @override
  String get ideCmdCloseEditor => 'قفل المحرر';

  @override
  String get ideCmdShowExplorer => 'إظهار المستكشف';

  @override
  String get ideCmdShowSourceControl => 'إظهار Source Control';

  @override
  String get ideCmdShowControlCenter => 'إظهار مركز تحكم AURA';

  @override
  String get ideCmdHideSidebar => 'إخفاء الشريط الجانبي';

  @override
  String get ideCmdShowSidebar => 'إظهار الشريط الجانبي';

  @override
  String get ideCmdHidePanel => 'إخفاء اللوحة';

  @override
  String get ideCmdShowPanel => 'إظهار اللوحة';

  @override
  String get ideCmdFocusTerminal => 'روح للتيرمينال';

  @override
  String get ideCmdHideAgent => 'إخفاء وكيل AURA';

  @override
  String get ideCmdShowAgent => 'إظهار وكيل AURA';

  @override
  String get ideCmdInlineCompletion => 'اطلب اقتراح كود في السطر';

  @override
  String get ideCmdRefreshSourceControl => 'تحديث Source Control';

  @override
  String get ideCmdLeaveIde => 'اخرج من الـ IDE (افتح النهارده)';

  @override
  String get ideStatusNoBranch => 'مفيش Branch';

  @override
  String get ideStatusHostNotRunning => 'الـ Host مش شغال';

  @override
  String get ideStatusHostNotPaired => 'الـ Host مش متربط';

  @override
  String get ideStatusHostOtherServer =>
      'الـ Host متربط بسيرفر تاني -- اربطه من جديد';

  @override
  String get ideStatusHostConnected => 'الـ Host متصل';

  @override
  String get ideStatusReconnecting => 'بنعيد الاتصال بالسيرفر...';

  @override
  String get ideStatusHostUnknown =>
      'حالة الـ Host مش معروفة (حدّث aura_hostd)';

  @override
  String ideStatusPendingApprovals(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count طلب موافقة مستني',
      many: '$count طلب موافقة مستني',
      few: '$count طلبات موافقة مستنية',
      two: 'طلبين موافقة مستنيين',
      one: 'طلب موافقة واحد مستني',
      zero: 'مفيش طلبات موافقة',
    );
    return '$_temp0';
  }

  @override
  String ideStatusRolledBackTooltip(String version) {
    return 'النسخة دي اترجعت لـ $version';
  }

  @override
  String ideStatusUpdateAvailableTooltip(String version) {
    return 'AURA $version متاح';
  }

  @override
  String ideStatusRollBackTo(String version) {
    return 'ارجع لـ $version';
  }

  @override
  String ideStatusUpdateTo(String version) {
    return 'حدّث لـ $version';
  }

  @override
  String ideStatusLineColumn(int line, int column) {
    return 'سطر $line، عمود $column';
  }

  @override
  String get ideStatusLineEndingMixed => 'مختلط';

  @override
  String ideUpdateRollbackTitle(String version) {
    return 'ترجع لـ AURA $version؟';
  }

  @override
  String ideUpdateTitle(String version) {
    return 'تحدّث لـ AURA $version؟';
  }

  @override
  String ideUpdateWindowsRollbackBody(String version) {
    return 'النسخة دي اترجعت. AURA هيتقفل، ويثبّت $version ويفتح تاني. التغييرات اللي مش محفوظة هتفضل وترجع زي ما هي.';
  }

  @override
  String ideUpdateWindowsBody(String version) {
    return 'AURA هيتقفل، ويثبّت $version ويفتح تاني. التغييرات اللي مش محفوظة هتفضل وترجع زي ما هي.';
  }

  @override
  String ideUpdateLinuxBody(String version) {
    return 'نزّل AURA $version للينكس من المتصفح.';
  }

  @override
  String get ideNotNow => 'مش دلوقتي';

  @override
  String get ideUpdateInstallRestart => 'تثبيت وإعادة تشغيل';

  @override
  String get ideUpdateDownload => 'تنزيل';

  @override
  String get ideUpdateDownloading => 'بننزّل التحديث...';

  @override
  String get ideUpdateChecksumFailed =>
      'الملف اللي اتنزّل مطابقش الـ checksum بتاع الإصدار واتمسح. مفيش حاجة اتثبّتت.';

  @override
  String ideUpdateFailed(String error) {
    return 'معرفناش نحدّث: $error';
  }

  @override
  String get idePendingApprovalsTitle => 'طلبات الموافقة المستنية';

  @override
  String ideApprovalFilesInRoot(int count, String root) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count ملف في $root',
      many: '$count ملف في $root',
      few: '$count ملفات في $root',
      two: 'ملفين في $root',
      one: 'ملف واحد في $root',
      zero: 'مفيش ملفات في $root',
    );
    return '$_temp0';
  }

  @override
  String ideApprovalFiles(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count ملف',
      many: '$count ملف',
      few: '$count ملفات',
      two: 'ملفين',
      one: 'ملف واحد',
      zero: 'مفيش ملفات',
    );
    return '$_temp0';
  }

  @override
  String get ideApprove => 'موافقة';

  @override
  String get explorerGrantTitle => 'تدّي hostd صلاحية على مساحة الشغل دي؟';

  @override
  String explorerGrantBody(String root) {
    return 'كده وكيل AURA هيقدر يقرا ويكتب ملفات في \"$root\".';
  }

  @override
  String get explorerGrant => 'اسمح';

  @override
  String get explorerAccessGranted => 'اتدّت صلاحية الوصول لمساحة الشغل';

  @override
  String explorerGrantFailed(String error) {
    return 'معرفناش ندّي الصلاحية: $error';
  }

  @override
  String get explorerHostOffline => 'الـ Host مش متصل';

  @override
  String get explorerNoWorkspaceTitle => 'مفيش مساحة شغل مفتوحة';

  @override
  String get explorerNoWorkspaceMessage =>
      'افتح فولدر من صفحة البداية علشان تتصفح ملفاته هنا.';

  @override
  String get explorerAccessRequiredTitle => 'محتاج صلاحية على مساحة الشغل';

  @override
  String get explorerLoadFailedTitle => 'فشل تحميل الفولدر';

  @override
  String get explorerAccessRequiredBody =>
      'الفولدر ده برّه كل الـ roots اللي الـ Host Daemon واخد صلاحية عليها. اسمح بالوصول علشان تتصفحه هنا ويقدر وكيل AURA يشتغل فيه.';

  @override
  String get explorerGrantAccess => 'اسمح بالوصول';

  @override
  String get explorerOutsideGrantedRoots =>
      'الفولدر ده برّه كل الـ roots اللي الـ Host Daemon واخد صلاحية عليها.';

  @override
  String get explorerHostUnreachable => 'مفيش اتصال بالـ Host Daemon.';

  @override
  String get explorerDirectoryNotFound => 'الفولدر مش موجود';

  @override
  String get explorerDeleteFolderTitle => 'تمسح الفولدر؟';

  @override
  String get explorerDeleteFileTitle => 'تمسح الملف؟';

  @override
  String explorerDeleteFolderBody(String name) {
    return '\"$name\" وكل اللي جواه هيتمسح نهائي.';
  }

  @override
  String explorerDeleteFileBody(String name) {
    return '\"$name\" هيتمسح نهائي.';
  }

  @override
  String get explorerRenameFolderTitle => 'تغيير اسم الفولدر';

  @override
  String get explorerRenameFileTitle => 'تغيير اسم الملف';

  @override
  String get explorerNewName => 'الاسم الجديد';

  @override
  String get explorerRename => 'تغيير الاسم';

  @override
  String explorerRenameFailed(String error) {
    return 'معرفناش نغيّر الاسم: $error';
  }

  @override
  String explorerDeleteFailed(String error) {
    return 'معرفناش نمسح: $error';
  }

  @override
  String get explorerNewFileTitle => 'ملف جديد';

  @override
  String get explorerFileName => 'اسم الملف';

  @override
  String get explorerCreate => 'إنشاء';

  @override
  String explorerCreateFileFailed(String error) {
    return 'معرفناش نعمل الملف: $error';
  }

  @override
  String get explorerNewFolderTitle => 'فولدر جديد';

  @override
  String get explorerFolderName => 'اسم الفولدر';

  @override
  String explorerCreateFolderFailed(String error) {
    return 'معرفناش نعمل الفولدر: $error';
  }

  @override
  String get explorerMenuOpen => 'فتح';

  @override
  String get explorerMenuCopyPath => 'نسخ المسار';

  @override
  String get explorerMenuCopyRelativePath => 'نسخ المسار النسبي';

  @override
  String get explorerMenuReveal => 'إظهار في نظام الملفات';

  @override
  String get explorerMenuOpenInTerminal => 'فتح في التيرمينال';

  @override
  String get explorerMenuCollapse => 'تصغير';

  @override
  String get explorerMenuExpand => 'توسيع';

  @override
  String get explorerMenuNewFile => 'ملف جديد';

  @override
  String get explorerMenuNewFolder => 'فولدر جديد';

  @override
  String get explorerAccessRequired => 'محتاج صلاحية';

  @override
  String get explorerLoadFailed => 'فشل التحميل';

  @override
  String get explorerEmpty => 'فاضي';

  @override
  String get explorerPathCopied => 'اتنسخ المسار';

  @override
  String get explorerRevealUnsupported =>
      'الإظهار في نظام الملفات مش مدعوم على المنصة دي';

  @override
  String explorerRevealFailed(String error) {
    return 'معرفناش نفتح نظام الملفات: $error';
  }

  @override
  String get gitDiffTruncated =>
      'الـ Diff اتقص -- شوف الـ Diff كامل في التيرمينال.';

  @override
  String get gitMergeDialogTitle => 'اعمل Merge لـ Branch في الـ Branch الحالي';

  @override
  String gitRemoteBranch(String name) {
    return '$name (ريموت)';
  }

  @override
  String get gitCreateBranchTitle => 'إنشاء Branch';

  @override
  String get gitBranchNameHint => 'اسم الـ Branch';

  @override
  String get gitNoWorkspace => 'افتح مساحة شغل علشان تستخدم Source Control.';

  @override
  String get gitGrantRequired =>
      'ادّي الـ Host Daemon صلاحية على مساحة الشغل دي علشان تستخدم Source Control.';

  @override
  String get gitFetchTooltip => 'Fetch من كل الـ remotes';

  @override
  String get gitPullTooltip => 'Pull (fast-forward بس)';

  @override
  String get gitPushTooltip => 'Push (حسب إعدادات الـ git بتاعتك)';

  @override
  String get gitMerge => 'Merge';

  @override
  String get gitBranch => 'Branch';

  @override
  String get gitCommitMessageHint => 'رسالة الـ Commit';

  @override
  String get gitWorking => 'شغالين...';

  @override
  String get gitCommit => 'Commit';

  @override
  String get gitHistory => 'السجل';

  @override
  String get gitNoCommits => 'مفيش Commits اتحمّلت.';

  @override
  String get gitStagedChanges => 'التغييرات اللي اتعملها Stage';

  @override
  String get gitUnstageAll => 'Unstage للكل';

  @override
  String get gitUnstage => 'Unstage';

  @override
  String get gitChanges => 'التغييرات';

  @override
  String get gitStageAll => 'Stage للكل';

  @override
  String get gitStage => 'Stage';

  @override
  String get gitNoChanges => 'مفيش تغييرات.';

  @override
  String get gitDetachedHead => '(detached HEAD)';

  @override
  String gitConflictCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count ملف فيهم Merge conflicts',
      many: '$count ملف فيهم Merge conflicts',
      few: '$count ملفات فيها Merge conflicts',
      two: 'ملفين فيهم Merge conflicts',
      one: 'ملف واحد فيه Merge conflicts',
      zero: 'مفيش ملفات فيها Merge conflicts',
    );
    return '$_temp0';
  }

  @override
  String get gitAbortMerge => 'إلغاء الـ Merge';

  @override
  String get gitMarkResolved => 'اعتبره اتحل';

  @override
  String get gitNothingStaged => 'مفيش حاجة متعملها Stage علشان تعمل Commit.';

  @override
  String gitBranchExists(String branch) {
    return 'فيه Branch اسمه \"$branch\" بالفعل. اختار اسم تاني، أو بدّل عليه من قايمة الـ Branches.';
  }

  @override
  String get gitCheckoutOverwrite => 'تغيير الـ Branch هيمسح تغييراتك المحلية.';

  @override
  String gitCheckoutOverwriteIn(String files) {
    return 'تغيير الـ Branch هيمسح تغييراتك المحلية في: $files';
  }

  @override
  String get gitNoUpstream =>
      'الـ Branch الحالي ملوش upstream. استخدم Publish Branch عشان تعمله Push.';

  @override
  String get gitDiverged =>
      'الـ Branch اتفرّع عن الـ remote -- الـ Pull (أو الـ Push) مينفعش يعمل fast-forward.';

  @override
  String get gitLocalChangesOverwritten => 'التغييرات المحلية هتتمسح.';

  @override
  String gitLocalChangesOverwrittenIn(String files) {
    return 'التغييرات المحلية هتتمسح في: $files';
  }

  @override
  String get gitRemoteSyncFailed => 'فشلت المزامنة مع الـ remote.';

  @override
  String get gitAuthFailed =>
      'تسجيل الدخول للـ remote فشل. سجّل دخولك للـ remote ده من الـ terminal أو من مدير بيانات الاعتماد، وبعدين جرّب تاني.';

  @override
  String get gitPublishBranch => 'Publish Branch';

  @override
  String get gitPublishTooltip =>
      'اعمل Push للـ Branch ده على remote وخليه الـ upstream بتاعه';

  @override
  String get gitPublishDialogTitle => 'Publish Branch';

  @override
  String gitPublishConfirm(String branch, String remote) {
    return 'تنشر \"$branch\" على \"$remote\" وتخليه الـ upstream بتاع الـ Branch ده؟';
  }

  @override
  String gitPublishChooseRemote(String branch) {
    return 'تنشر \"$branch\" على أنهي remote؟ هيبقى هو الـ upstream بتاع الـ Branch ده.';
  }

  @override
  String get gitPublishAction => 'انشر';

  @override
  String get gitNoRemote =>
      'المشروع ده معندوش remote تنشر عليه. ضيف واحد الأول (git remote add).';

  @override
  String get gitPublishUnknownRemote => 'الـ remote ده مبقاش متظبط.';

  @override
  String get gitPublishDetachedHead =>
      'اعمل Checkout لـ Branch الأول، الـ HEAD المنفصل مينفعش يتنشر.';

  @override
  String get gitPublishNoCommits =>
      'اعمل أول Commit قبل ما تنشر الـ Branch ده.';

  @override
  String get gitPublishDiverged =>
      'الـ remote فيه Branch تاني بنفس الاسم ومختلف عن بتاعك.';

  @override
  String get workspaceWelcomeSubtitle => 'افتح فولدر وابدأ تكتب كود مع AURA';

  @override
  String get workspaceOpenFolder => 'فتح فولدر';

  @override
  String get workspaceRecent => 'الأخيرة';

  @override
  String workspaceGrantBody(String path) {
    return 'كده وكيل AURA هيقدر يقرا ويكتب ملفات في \"$path\".';
  }

  @override
  String get workspaceNotNow => 'مش دلوقتي';

  @override
  String get workspaceNoRecent => 'مفيش مساحات شغل اتفتحت قريب';

  @override
  String get workspaceNoRecentHint => 'افتح فولدر علشان تبدأ';

  @override
  String get workspaceRemoveFromRecent => 'شيلها من الأخيرة';

  @override
  String get workspaceManagerTitle => 'مساحات الشغل';

  @override
  String get workspaceRecentWorkspaces => 'مساحات الشغل الأخيرة';

  @override
  String get workspaceActive => 'الحالية';

  @override
  String workspaceLastOpened(String date) {
    return 'آخر فتح: $date';
  }

  @override
  String get workspaceOpen => 'فتح';

  @override
  String get workspaceRemoveTitle => 'تشيل مساحة الشغل؟';

  @override
  String workspaceRemoveBody(String name) {
    return 'كده \"$name\" هتتشال من مساحات الشغل الأخيرة.';
  }

  @override
  String get workspaceRemove => 'إزالة';

  @override
  String get workspaceEmptyTitle => 'لسه مفيش مساحات شغل';

  @override
  String get workspaceEmptyMessage => 'افتح فولدر علشان تعمل أول مساحة شغل ليك';

  @override
  String relativeTodayAt(String time) {
    return 'النهارده $time';
  }

  @override
  String get relativeYesterday => 'امبارح';

  @override
  String relativeDaysAgo(int days) {
    String _temp0 = intl.Intl.pluralLogic(
      days,
      locale: localeName,
      other: 'من $days يوم',
      many: 'من $days يوم',
      few: 'من $days أيام',
      two: 'من يومين',
      one: 'من يوم',
      zero: 'النهارده',
    );
    return '$_temp0';
  }

  @override
  String get coreEngineerWorkspacesUnavailableTitle => 'مساحات الشغل مش متاحة';

  @override
  String get coreEngineerWorkspacesUnavailableMessage =>
      'مقدرناش نوصل للسيرفر. شغلك مضاعش -- جرّب تاني كمان شوية.';

  @override
  String get coreEngineerWorkspacesDisabledTitle => 'مساحات الشغل مش مفعّلة';

  @override
  String get coreEngineerWorkspacesDisabledMessage =>
      'السيرفر ده مش بيشغّل الـ sandboxes بتاعة المهندس.';

  @override
  String get coreEngineerWorkspacesEmptyTitle => 'مفيش مساحات شغل لسه';

  @override
  String coreEngineerWorkspacesEmptyMessage(int hours) {
    return 'اطلب من المهندس يعمل clone لريبو وهتظهر مساحة شغل هنا. مساحات الشغل بتتمسح بعد $hours ساعة من غير أي نشاط.';
  }

  @override
  String get coreEngineerWorkspacesHeader => 'مساحات شغل المهندس';

  @override
  String coreEngineerWorkspaceSemanticLabel(String repo) {
    return 'مساحة شغل لـ $repo';
  }

  @override
  String coreEngineerWorkspaceBranch(String branch) {
    return 'Branch $branch';
  }

  @override
  String get coreEngineerWorkspaceDeleteTitle => 'عايز تمسح مساحة الشغل دي؟';

  @override
  String get coreEngineerWorkspaceKeep => 'سيبها';

  @override
  String coreEngineerWorkspaceDeleteBody(String repo) {
    return 'نسخة $repo وأي تغييرات مش متعملها Commit فيها هتتمسح. أي حاجة اتعملها Push قبل كده مش هتتأثر.';
  }

  @override
  String get coreEngineerWorkspaceDeleted => 'مساحة الشغل اتمسحت.';

  @override
  String get coreEngineerWorkspaceDeleteFailed =>
      'مقدرناش نمسح مساحة الشغل دي.';

  @override
  String get coreEngineerWorkspaceExpiryOverdue => 'هتتمسح دلوقتي';

  @override
  String get coreEngineerWorkspaceExpiryUnderHour =>
      'هتتمسح في أقل من ساعة لو ما اتستخدمتش';

  @override
  String coreEngineerWorkspaceExpiryHours(int hours) {
    String _temp0 = intl.Intl.pluralLogic(
      hours,
      locale: localeName,
      other: 'هتتمسح خلال $hours ساعة لو ما اتستخدمتش',
      many: 'هتتمسح خلال $hours ساعة لو ما اتستخدمتش',
      few: 'هتتمسح خلال $hours ساعات لو ما اتستخدمتش',
      two: 'هتتمسح خلال ساعتين لو ما اتستخدمتش',
      one: 'هتتمسح خلال ساعة لو ما اتستخدمتش',
    );
    return '$_temp0';
  }

  @override
  String coreEngineerWorkspaceExpiryDays(int days) {
    String _temp0 = intl.Intl.pluralLogic(
      days,
      locale: localeName,
      other: 'هتتمسح خلال $days يوم لو ما اتستخدمتش',
      many: 'هتتمسح خلال $days يوم لو ما اتستخدمتش',
      few: 'هتتمسح خلال $days أيام لو ما اتستخدمتش',
      two: 'هتتمسح خلال يومين لو ما اتستخدمتش',
      one: 'هتتمسح خلال يوم لو ما اتستخدمتش',
    );
    return '$_temp0';
  }

  @override
  String get coreEngineerWorkspaceRunning => 'شغّالة';

  @override
  String get coreEngineerWorkspaceIdle => 'واقفة';

  @override
  String get voiceClientNothingToRead => 'مفيش حاجة تتقري في الرد ده.';

  @override
  String get voiceClientSpeechFailed => 'مقدرناش نحوّل الرد ده لصوت.';

  @override
  String get voiceClientUnreachable =>
      'مقدرناش نوصل لـ AURA علشان نطلّع الصوت.';

  @override
  String get voiceClientSignInAgain => 'سجّل الدخول تاني علشان تكمّل.';

  @override
  String get voiceClientSpeakNotAvailable =>
      'تشغيل الصوت مش متاح على السيرفر ده.';

  @override
  String get voiceClientReplyTooLong => 'الرد ده طويل أوي إنه يتقري بصوت عالي.';

  @override
  String get voiceClientRateLimited =>
      'طلبات صوت كتير أوي. جرّب تاني كمان شوية.';

  @override
  String get voiceClientNoSpeechEngine => 'السيرفر ده مفيهوش محرك صوت متظبط.';

  @override
  String get voiceSocketSessionExpired =>
      'الصوت المباشر وقف علشان تسجيل الدخول محتاج يتجدد. ابدأه تاني.';

  @override
  String get voiceSocketDisconnected => 'الصوت المباشر اتقطع. ابدأه تاني.';

  @override
  String get coreBackendEnvironmentProduction => 'Production';

  @override
  String get coreBackendEnvironmentDevelopment => 'Development';

  @override
  String get coreAskCopilotTooltip => 'اسأل Copilot';

  @override
  String get coreDesktopSignInBrowserFailed => 'مقدرناش نفتح المتصفح.';

  @override
  String get coreDesktopSignInTimedOut =>
      'الوقت خلص واحنا مستنيين تسجيل الدخول من المتصفح يخلص.';

  @override
  String coreDesktopSignInPageCancelled(String error) {
    return 'تسجيل الدخول اتلغى ($error).';
  }

  @override
  String coreDesktopSignInGoogleError(String error) {
    return 'Google رجّع مشكلة: $error';
  }

  @override
  String get coreDesktopSignInPageInvalidResponse =>
      'تسجيل الدخول فشل: الرد مش سليم.';

  @override
  String get coreDesktopSignInInvalidRedirect =>
      'الـ authorization code ناقص أو الـ state مش مطابق (ممكن يكون CSRF).';

  @override
  String get coreDesktopSignInPageSuccess =>
      'سجّلت الدخول -- تقدر تقفل التاب ده.';

  @override
  String get coreDesktopSignInServerUnreachable =>
      'مقدرناش نوصل لسيرفر AURA علشان نكمّل تسجيل الدخول. اتأكد من الاتصال وجرّب تاني.';

  @override
  String get coreDesktopSignInServerInsecure =>
      'مقدرناش نعمل اتصال آمن بسيرفر AURA علشان نكمّل تسجيل الدخول.';

  @override
  String get coreDesktopSignInServerBadAnswer =>
      'سيرفر AURA مردّش صح واحنا بنكمّل تسجيل الدخول. جرّب تاني.';

  @override
  String get coreDesktopSignInServerTimedOut =>
      'سيرفر AURA أخد وقت طويل أوي في تسجيل الدخول. جرّب تاني.';

  @override
  String coreDesktopSignInServerFailed(int status) {
    return 'سيرفر AURA مقدرش يكمّل تسجيل الدخول (HTTP $status).';
  }

  @override
  String get coreDesktopSignInNoIdentityToken =>
      'سيرفر AURA مرجّعش identity token.';

  @override
  String get coreDesktopSignInPageSignedIn => 'سجّلت الدخول';

  @override
  String get coreDesktopSignInPageFailed => 'تسجيل الدخول فشل';

  @override
  String get failureFeatureUnavailableTitle => 'ده محتاج ميزة لسه مش متاحة';

  @override
  String get failureFeatureUnavailableMessage =>
      'AURA يقدر يجاوب على الأسئلة العادية دلوقتي. الإجابات اللي بتستخدم الذاكرة أو المستندات أو الأدوات أو الصور جاية قريب.';

  @override
  String get failureSignInRefusedTitle => 'AURA مقبلش تسجيل الدخول ده';

  @override
  String get failureSignInRefusedMessage =>
      'السيرفر رفض الحساب ده، فمتسجّلتش. جرّب حساب تاني، أو اطلب من اللي مشغّل سيرفر AURA ده يراجع الحساب ده.';

  @override
  String get failureDesktopPlanRequiredTitle =>
      'تطبيق الكمبيوتر محتاج باقة Pro أو Team';

  @override
  String get failureDesktopPlanRequiredMessage =>
      'الـEngineer على الكمبيوتر بتاعك جزء من باقتي Pro وTeam. رقّي باقتك، وبعدين سجّل دخول تاني.';

  @override
  String get failureSignInIncompleteTitle => 'مقدرناش نكمّل تسجيل الدخول';

  @override
  String get failureUnclassifiedMessage =>
      'AURA قابل مشكلة مش عارف يحدد نوعها. شغلك ماتأثرش.';

  @override
  String get failureUnreachableTitle => 'مقدرناش نوصل لـ AURA';

  @override
  String get failureUnreachableMessage =>
      'محدش ردّ — ممكن يكون الاتصال عندك أو السيرفر. أي حاجة حفظتها قبل كده موجودة بأمان على الجهاز ده.';

  @override
  String get failureInferenceUnavailableTitle =>
      'AURA وصل للسيرفر، بس الموديل مش بيرد';

  @override
  String get failureInferenceUnavailableMessage =>
      'الـ Control Plane شغّال، بس الـ inference لأ. مساحة الشغل والمحادثات بتاعتك زي ما هي.';

  @override
  String get failureBusyTitle => 'AURA مشغول';

  @override
  String get failureBusyMessage =>
      'في حاجات كتير شغّالة في نفس الوقت. استنى لحظة وغالبًا هتتحل.';

  @override
  String get failureRateLimitedTitle => 'طلبات كتير أوي';

  @override
  String get failureRateLimitedMessage =>
      'وصلت للحد الأقصى للطلبات. جرّب تاني كمان شوية.';

  @override
  String get failureTurnInFlightTitle => 'AURA لسه شغّال على طلبك اللي فات';

  @override
  String get failureTurnInFlightMessage =>
      'طلب واحد بس بيشتغل في المرة. استنى لحد ما يخلص، أو وقّفه الأول.';

  @override
  String get failureAuthRequiredTitle => 'سجّل الدخول تاني';

  @override
  String get failureAuthRequiredMessage =>
      'الجلسة بتاعتك خلصت. لما تسجّل الدخول تاني هتكمّل من نفس المكان اللي وقفت عنده.';

  @override
  String get failureForbiddenTitle => 'مش مسموح';

  @override
  String get failureForbiddenMessage =>
      'حسابك ماعندوش صلاحية للحاجة دي. وإنك تجرّب تاني مش هيغيّر ده.';

  @override
  String get failureBudgetTitle => 'الميزانية خلصت';

  @override
  String get failureBudgetMessage => 'الطلب ده هيعدّي حد الصرف المتظبط.';

  @override
  String get failureBlockedTitle => 'AURA رفض الطلب ده';

  @override
  String get failureBlockedMessage =>
      'اترفض لأسباب تخص الأمان. لو بعت نفس الحاجة تاني هتترفض بنفس الطريقة — إنك تعيد الصياغة هو اللي هيغيّر النتيجة.';

  @override
  String get failureIndexingTitle => 'لسه بيعمل فهرسة';

  @override
  String get failureIndexingMessage =>
      'AURA بيبني فهرس البحث للمحتوى ده. هيشتغل أول ما يخلص.';

  @override
  String get failureDegradedTitle => 'AURA شغّال بأداء أقل';

  @override
  String get failureDegradedMessage =>
      'جزء من النظام مردّش في الوقت المطلوب. بياناتك ماتأثرتش.';

  @override
  String get failureServerErrorTitle => 'AURA حصلت عنده مشكلة';

  @override
  String get failureServerErrorMessage =>
      'السيرفر فشل إنه يكمّل ده. بياناتك ماتأثرتش.';

  @override
  String get failureUnknownMessage =>
      'AURA مقدرش يكمّل الطلب ده. شغلك ماتأثرش.';

  @override
  String failureRateLimitedWaitSeconds(int seconds) {
    String _temp0 = intl.Intl.pluralLogic(
      seconds,
      locale: localeName,
      other: '$seconds ثانية',
      many: '$seconds ثانية',
      few: '$seconds ثواني',
      two: 'ثانيتين',
      one: 'ثانية',
    );
    return 'وصلت للحد الأقصى للطلبات. جرّب تاني بعد $_temp0.';
  }

  @override
  String failureRateLimitedWaitMinutes(int minutes) {
    String _temp0 = intl.Intl.pluralLogic(
      minutes,
      locale: localeName,
      other: '$minutes دقيقة',
      many: '$minutes دقيقة',
      few: '$minutes دقايق',
      two: 'دقيقتين',
      one: 'دقيقة',
    );
    return 'وصلت للحد الأقصى للطلبات. جرّب تاني بعد حوالي $_temp0.';
  }

  @override
  String get coreApiErrorUnreachable => 'مقدرناش نوصل لـ AURA.';

  @override
  String coreApiErrorUnreachableCause(String cause) {
    return 'مقدرناش نوصل لـ AURA: $cause';
  }

  @override
  String get coreApiErrorBlocked => 'AURA وقّف الطلب ده.';

  @override
  String get coreApiErrorSignInAgain => 'سجّل الدخول تاني علشان تكمّل.';

  @override
  String get coreApiErrorForbidden => 'ماعندكش صلاحية تعمل ده.';

  @override
  String get coreApiErrorRateLimited => 'طلبات كتير أوي. جرّب تاني كمان شوية.';

  @override
  String get coreApiErrorStartingUp => 'AURA لسه بيقوم. جرّب تاني كمان لحظة.';

  @override
  String coreApiErrorRequestFailed(int status) {
    return 'الطلب فشل ($status).';
  }

  @override
  String get coreSyncSynced => 'اتعمل Sync';

  @override
  String get coreSyncSyncing => 'جاري الـ Sync';

  @override
  String get coreSyncOffline => 'مفيش اتصال';

  @override
  String get coreSyncFailed => 'الـ Sync فشل';

  @override
  String coreSyncPendingRefused(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count تغيير ما اتعملهاش Sync',
      many: '$count تغيير ما اتعملهاش Sync',
      few: '$count تغييرات ما اتعملهاش Sync',
      two: 'تغييرين ما اتعملهمش Sync',
      one: 'تغيير واحد ما اتعملوش Sync',
    );
    return '$_temp0';
  }

  @override
  String coreSyncStatusSemantic(String label) {
    return 'حالة الـ Sync: $label';
  }

  @override
  String coreSyncParkedBanner(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: 'في $count تغيير موصلوش للسيرفر. هما محفوظين على الجهاز ده.',
      many: 'في $count تغيير موصلوش للسيرفر. هما محفوظين على الجهاز ده.',
      few: 'في $count تغييرات موصلوش للسيرفر. هما محفوظين على الجهاز ده.',
      two: 'في تغييرين موصلوش للسيرفر. هما محفوظين على الجهاز ده.',
      one: 'في تغيير واحد موصلش للسيرفر. هو محفوظ على الجهاز ده.',
    );
    return '$_temp0';
  }

  @override
  String coreSyncParkedBannerSemantic(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: 'في $count تغيير موصلوش للسيرفر. هما محفوظين على الجهاز ده.',
      many: 'في $count تغيير موصلوش للسيرفر. هما محفوظين على الجهاز ده.',
      few: 'في $count تغييرات موصلوش للسيرفر. هما محفوظين على الجهاز ده.',
      two: 'في تغييرين موصلوش للسيرفر. هما محفوظين على الجهاز ده.',
      one: 'في تغيير واحد موصلش للسيرفر. هو محفوظ على الجهاز ده.',
    );
    return '$_temp0';
  }

  @override
  String get coreSyncReview => 'مراجعة';

  @override
  String get coreHostdNotRunning => 'aura_hostd مش شغّال.';

  @override
  String coreHostdUnreachable(String error) {
    return 'مقدرناش نوصل لـ aura_hostd: $error';
  }

  @override
  String get coreHostdOutOfDate =>
      'الـ aura_hostd اللي على جهازك مش فاهم الطلب ده -- ممكن يكون قديم. اعمل Build تاني لـ aura_hostd وشغّله من الأول، وبعدين جرّب تاني.';

  @override
  String get coreHostdNotRunningStartIt =>
      'aura_hostd مش شغّال. شغّله وجرّب تاني.';

  @override
  String get coreHostdNotOnWeb =>
      'الـ Host Daemon بتاع المهندس مش متاح على الويب.';

  @override
  String get coreHostdRejectedArguments =>
      'aura_hostd رفض الـ arguments بتاعته (exit 64) -- راجع الملف التنفيذي المتظبط';

  @override
  String coreHostdCrashedRepeatedly(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: 'aura_hostd وقع $count مرة ورا بعض؛ شوف الـ log اللي فوق',
      many: 'aura_hostd وقع $count مرة ورا بعض؛ شوف الـ log اللي فوق',
      few: 'aura_hostd وقع $count مرات ورا بعض؛ شوف الـ log اللي فوق',
      two: 'aura_hostd وقع مرتين ورا بعض؛ شوف الـ log اللي فوق',
      one: 'aura_hostd وقع مرة؛ شوف الـ log اللي فوق',
    );
    return '$_temp0';
  }

  @override
  String coreHostdRestartFailed(String error) {
    return 'مقدرناش نعيد تشغيل aura_hostd: $error';
  }

  @override
  String coreCommandItemUnavailable(String title, String reason) {
    return '$title، مش متاح: $reason';
  }

  @override
  String chatActionCreateTask(String title) {
    return 'إنشاء مهمة: $title';
  }

  @override
  String chatActionScheduleEvent(String title) {
    return 'تحديد ميعاد: $title';
  }

  @override
  String chatActionLogExpense(String amount) {
    return 'تسجيل مصروف: \$$amount';
  }

  @override
  String chatActionLogIncome(String amount) {
    return 'تسجيل دخل: \$$amount';
  }

  @override
  String get chatActionLogHealth => 'تسجيل الصحة';

  @override
  String chatActionUnknownNamed(String type) {
    return 'إجراء مش معروف: $type';
  }

  @override
  String get chatActionUnknown => 'إجراء مش معروف';

  @override
  String get chatSpeechGenerateFailed => 'معرفناش نطلّع صوت للرد ده.';

  @override
  String get chatSpeechPlayFailed => 'معرفناش نشغّل الصوت.';

  @override
  String get chatDefaultImageQuestion => 'إيه اللي في الصورة دي؟';

  @override
  String get chatEngineUnreachable => 'AURA (مفيش اتصال)';

  @override
  String get chatEngineServer => 'سيرفر AURA';

  @override
  String chatCreateBranchOnRepo(String repo) {
    return 'إنشاء Branch في $repo';
  }

  @override
  String get chatCreateBranch => 'إنشاء Branch';

  @override
  String get chatConfirmActions => 'تأكيد الإجراءات';

  @override
  String get chatConfirmAction => 'تأكيد الإجراء';

  @override
  String get chatConfirmSelected => 'تأكيد المختار';

  @override
  String get chatConfirmSwitchBranch => 'التحويل لـ Branch';

  @override
  String get chatConfirmOpenDraftPr => 'فتح Draft Pull Request';

  @override
  String chatConfirmCommitFiles(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: 'لـ $count ملف',
      many: 'لـ $count ملف',
      few: 'لـ $count ملفات',
      two: 'لملفين',
      one: 'لملف واحد',
      zero: 'لـ 0 ملفات',
    );
    return 'Commit $_temp0';
  }

  @override
  String chatConfirmCommitFilesToBranch(int count, String branch) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: 'لـ $count ملف',
      many: 'لـ $count ملف',
      few: 'لـ $count ملفات',
      two: 'لملفين',
      one: 'لملف واحد',
      zero: 'لـ 0 ملفات',
    );
    return 'Commit $_temp0 على $branch';
  }

  @override
  String get chatConfirmWriteFile => 'كتابة ملف';

  @override
  String chatConfirmEditFile(String path) {
    return 'تعديل $path';
  }

  @override
  String get chatConfirmEditFileUnnamed => 'تعديل ملف';

  @override
  String get chatConfirmRunCommand => 'تشغيل أمر';

  @override
  String get chatConfirmCommitSandbox => 'Commit لتغييرات الـ Sandbox';

  @override
  String get chatTurnQuarantined =>
      'AURA اعتبر المحتوى اللي قراه مش موثوق، فمفيش حاجة ليها تأثير هتشتغل من غير تأكيدك.';

  @override
  String get chatTurnInterrupted => 'اتقطع — الرد ده مش كامل.';

  @override
  String get chatTurnRedacted => 'الرد ده اتعدّل بفحص أمان.';

  @override
  String chatTurnLowConfidence(int percent) {
    return 'ثقة قليلة ($percent%).';
  }

  @override
  String get chatTurnLibraryEmpty =>
      'دوّرنا في مكتبتك — ملقيناش حاجة ليها علاقة.';

  @override
  String get chatTurnReducedMode => 'AURA اشتغل في الدور ده بوضع محدود.';

  @override
  String get chatTurnUngrounded => 'من غير مصادر — مفيش مصادر مرفقة.';

  @override
  String chatMoreLines(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count سطر كمان',
      many: '$count سطر كمان',
      few: '$count سطور كمان',
      two: 'سطرين كمان',
      one: 'سطر كمان',
      zero: 'مفيش سطور تانية',
    );
    return '… $_temp0';
  }

  @override
  String chatImageReadFailed(String name) {
    return 'معرفناش نقرا $name.';
  }

  @override
  String chatImageTooMany(int max) {
    return 'تقدر ترفق لحد $max صور في الرسالة الواحدة.';
  }

  @override
  String chatImageTooLarge(String name, String mb) {
    return '$name أكبر من $mb MB.';
  }

  @override
  String chatImagesAttached(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count صورة مرفقة',
      many: '$count صورة مرفقة',
      few: '$count صور مرفقة',
      two: 'صورتين مرفقين',
      one: 'صورة واحدة مرفقة',
      zero: 'مفيش صور مرفقة',
    );
    return '$_temp0';
  }

  @override
  String chatActionCreateTaskDetail(
    String title,
    String priority,
    String category,
  ) {
    return 'إنشاء مهمة: $title (أولوية $priority، $category)';
  }

  @override
  String chatActionScheduleEventAt(String title, String start) {
    return 'تحديد ميعاد: $title في $start';
  }

  @override
  String chatActionLogExpenseFor(String amount, String category) {
    return 'تسجيل مصروف: \$$amount على $category';
  }

  @override
  String chatActionLogIncomeFor(String amount, String category) {
    return 'تسجيل دخل: \$$amount من $category';
  }

  @override
  String chatHealthWater(String ml) {
    return '${ml}ml مية';
  }

  @override
  String chatHealthSleep(String minutes) {
    return '$minutes دقيقة نوم';
  }

  @override
  String chatHealthExercise(String minutes) {
    return '$minutes دقيقة تمرين';
  }

  @override
  String chatActionLogHealthDetail(String metrics) {
    return 'تسجيل الصحة: $metrics';
  }

  @override
  String get chatListSeparator => '، ';

  @override
  String get chatStepFallback => 'خطوة';

  @override
  String chatStepBlocked(String detail) {
    return 'متوقفة: $detail';
  }

  @override
  String get chatCapabilityEngineer => 'صلاحية المهندس';

  @override
  String get chatCapabilityAdditional => 'صلاحية إضافية';

  @override
  String chatPersonaRequires(String capability) {
    return 'محتاج $capability -- مش متاحة على الحساب ده';
  }

  @override
  String chatPersonaSwitchToUse(String workspace) {
    return 'موجود في $workspace -- دوس عشان تنقل لها وتستخدم الـEngineer';
  }

  @override
  String get chatWithheldNotice => 'الرد ده اتحجب بفحص أمان.';

  @override
  String get chatBlockedNotice => 'AURA مردّش على الطلب ده: فحص أمان وقّفه.';

  @override
  String get chatFeatureUnavailableNotice =>
      'ده محتاج ميزة لسه مش متاحة. AURA يقدر يرد على الأسئلة العادية دلوقتي. الردود اللي بتستخدم الذاكرة أو المستندات أو الأدوات أو الصور جاية قريب.';

  @override
  String get chatPersonaOrchestrator => 'المنسّق';

  @override
  String get chatPersonaTaskPlanner => 'مخطط المهام';

  @override
  String get chatPersonaFinanceAdvisor => 'المستشار المالي';

  @override
  String get chatPersonaHealthCompanion => 'رفيق الصحة';

  @override
  String get chatPersonaResearchAgent => 'وكيل البحث';

  @override
  String get chatPersonaCodingAgent => 'وكيل البرمجة';

  @override
  String get chatPersonaDecisionAgent => 'وكيل القرارات';

  @override
  String get chatPersonaEngineer => 'المهندس';

  @override
  String chatHeaderStatus(String model, String status, String latency) {
    return 'نشط • $model ($status) • ${latency}ms';
  }

  @override
  String get memoryFilterAll => 'الكل';

  @override
  String get memoryEmptyTitle => 'مفيش ذكريات لسه';

  @override
  String get memoryEmptyMessage =>
      'احفظ معلومة ومعاها نوعها علشان Copilot يقدر يستخدمها بعدين.';

  @override
  String get memoryForgotten => 'الذكرى اتمسحت';

  @override
  String get memoryForgottenLocally =>
      'اتمسحت من الجهاز ده. AURA هيكمّل مسحها من السيرفر أول ما يقدر يوصله.';

  @override
  String memoryForgetFailed(String reason) {
    return 'معرفناش نمسح الذكرى دي. $reason';
  }

  @override
  String get memoryAddTitle => 'افتكر ده';

  @override
  String get memoryFactLabel => 'المعلومة';

  @override
  String get memorySubjectLabel => 'الموضوع (اختياري)';

  @override
  String get memoryKindField => 'النوع';

  @override
  String get memoryKindWorking => 'مؤقتة';

  @override
  String get memoryKindEpisodic => 'أحداث';

  @override
  String get memoryKindSemantic => 'معلومات';

  @override
  String get memoryKindEntity => 'كيانات';

  @override
  String get memoryKindProcedural => 'طرق وخطوات';

  @override
  String get memoryKindReflection => 'تأملات';

  @override
  String get projectsWelcomeBack => 'أهلاً بيك تاني،';

  @override
  String get projectsDefaultUserName => 'مستخدم AURA';

  @override
  String get projectsTitle => 'المشاريع';

  @override
  String get projectsSubtitle => 'نظّم شغلك في مشاريع';

  @override
  String get projectsNew => 'مشروع جديد';

  @override
  String get projectsEmptyTitle => 'مفيش مشاريع لسه';

  @override
  String get projectsEmptyMessage => 'اعمل أول مشروع ليك علشان تبدأ';

  @override
  String get projectsCreateTitle => 'إنشاء مشروع';

  @override
  String get projectsTitleField => 'العنوان *';

  @override
  String get projectsDescriptionField => 'الوصف';

  @override
  String get projectsStatusField => 'الحالة';

  @override
  String get projectsSetTargetDate => 'حدد تاريخ مستهدف (اختياري)';

  @override
  String projectsTarget(String date) {
    return 'المستهدف: $date';
  }

  @override
  String get projectsCreate => 'إنشاء';

  @override
  String projectsDue(String date) {
    return 'التسليم: $date';
  }

  @override
  String get projectsStatusActive => 'نشط';

  @override
  String get projectsStatusCompleted => 'خلصان';

  @override
  String get projectsStatusArchived => 'متأرشف';

  @override
  String get projectsStatusOnHold => 'متوقف مؤقتاً';

  @override
  String get analyticsTitle => 'لوحة تحليلات النظام';

  @override
  String get analyticsEmptyTitle => 'لسه مفيش قياسات اتجمعت';

  @override
  String get analyticsEmptyMessage => 'القياسات هتظهر أول ما تبعت رسايل.';

  @override
  String get analyticsTotalRequests => 'إجمالي الطلبات';

  @override
  String get analyticsAvgLatency => 'متوسط زمن الاستجابة';

  @override
  String get analyticsFailovers => 'التحويلات الاحتياطية';

  @override
  String get analyticsErrorsLogged => 'المشاكل المسجّلة';

  @override
  String get analyticsLatencyHistory => 'سجل زمن الاستجابة (آخر 10 طلبات)';

  @override
  String get analyticsInsufficientData => 'البيانات مش كفاية';

  @override
  String get analyticsInferencePaths => 'توزيع مسارات الاستدلال';

  @override
  String get analyticsNoInferenceData => 'مفيش بيانات استدلال';

  @override
  String get analyticsFailoverStats => 'إحصائيات التحويل الاحتياطي للـ API';

  @override
  String get analyticsNoFailoverEvents => 'مفيش تحويلات احتياطية';

  @override
  String analyticsSuccessCount(int count) {
    return 'نجاح ($count)';
  }

  @override
  String analyticsFailoversCount(int count) {
    return 'تحويلات احتياطية ($count)';
  }

  @override
  String get analyticsLatestLogs => 'أحدث سجلات القياس';

  @override
  String get lifeHubFilterAll => 'الكل';

  @override
  String get lifeHubFilterTasks => 'المهام';

  @override
  String get lifeHubFilterCalendar => 'التقويم';

  @override
  String get lifeHubFilterFinance => 'الماليات';

  @override
  String get lifeHubFilterHealth => 'الصحة';

  @override
  String get lifeHubFilterProjects => 'المشاريع';

  @override
  String get lifeHubFilterGoals => 'الأهداف';

  @override
  String get lifeHubAgendaEmptyTitle => 'مفيش حاجة على اللوحة';

  @override
  String get lifeHubAgendaEmptyMessage =>
      'ضيف مهمة أو ميعاد أو تسجيل علشان تشوف الأسبوع ده كله في مكان واحد.';

  @override
  String get lifeHubHealthLogTitle => 'سجل الصحة النهارده';

  @override
  String lifeHubHealthLogWater(int ml) {
    return '$ml ml مية';
  }

  @override
  String get lifeHubNoUpcomingEvents => 'مفيش مواعيد جاية';

  @override
  String get lifeHubProjectsEmptyMessage => 'اعمل مشروع علشان تنظّم شغلك.';

  @override
  String get lifeHubGoalsEmptyTitle => 'مفيش أهداف لسه';

  @override
  String get lifeHubGoalsEmptyMessage => 'اعمل هدف علشان تتابع طموحاتك.';

  @override
  String lifeHubGoalProgress(int percent) {
    return '$percent% خلصان';
  }

  @override
  String get voiceLiveTranscript => 'النص المكتوب';

  @override
  String get voiceLiveInterrupt => 'مقاطعة';

  @override
  String get voiceLiveEndShort => 'إنهاء';

  @override
  String get voiceLiveUnsupported => 'الصوت المباشر متاح على Android و iOS بس';

  @override
  String get voiceLiveAudioUnavailable =>
      'الصوت مش متاح على الجهاز ده، الردود هتظهر كتابة بس';

  @override
  String get homeAskAuraLabel => 'اسأل AURA أي حاجة';

  @override
  String get homeSendToAura => 'ابعت لـ AURA';

  @override
  String get homeInsightsLoadFailed => 'معرفناش نحمّل الملاحظات';

  @override
  String get homeInsightsLoadFailedMessage =>
      'اسحب لتحت علشان تحدّث لما النت يرجع.';

  @override
  String get homeDismissInsight => 'تجاهل الملاحظة';

  @override
  String homeDueTask(String title) {
    return 'مهمة: $title';
  }

  @override
  String homeDueEvent(String title) {
    return 'ميعاد: $title';
  }

  @override
  String homeDueOverdue(String item) {
    return '$item، متأخر';
  }

  @override
  String get notificationsFailoverTitle => 'تحويل احتياطي في السيرفر';

  @override
  String notificationsFailoverMessage(String model) {
    return 'AURA مقدرش يكمّل الطلب على $model وسجّل تحويل احتياطي.';
  }

  @override
  String get notificationsRequestFailedTitle => 'الطلب فشل';

  @override
  String notificationsRequestFailedMessage(String model) {
    return 'طلب AI فشل على $model.';
  }

  @override
  String get notificationsEmptyTitle => 'مفيش حاجة جديدة';

  @override
  String get notificationsEmptyMessage =>
      'هتلاقي هنا التحويلات الاحتياطية ومشاكل الطلبات.';

  @override
  String get notificationsLoadFailed => 'معرفناش نحمّل الإشعارات';

  @override
  String get feedbackEmptyTitle => 'مفيش تصحيحات لسه';

  @override
  String get feedbackEmptyMessage =>
      'قيّم رد من Copilot علشان تساعد AURA يتعلم إنت بتفضّل إيه.';

  @override
  String feedbackRated(int rating, String date) {
    return 'التقييم $rating/5 · $date';
  }

  @override
  String modelsLastResponse(String ms) {
    return 'آخر رد خد $ms ms';
  }

  @override
  String get modelsNoRequestsYet => 'مفيش طلبات لسه في الجلسة دي';

  @override
  String get modelsCheckAgain => 'افحص تاني';

  @override
  String get modelsServerNote =>
      'الطلبات بتتعالج على سيرفرات AURA. مفيش حاجة تنزّلها أو تظبطها على الجهاز ده.';

  @override
  String get paletteOpenDesktopIde => 'فتح الـ Desktop IDE';

  @override
  String get paletteSearchHint => 'دوّر في AURA أو روح لشاشة…';

  @override
  String get paletteGoTo => 'روح لـ';

  @override
  String get paletteNoMatches => 'مفيش نتايج';

  @override
  String get authAttestationDebugOnly =>
      'علامة Debug بس — توثيق الهاردوير مش مفعّل في التطبيق ده';

  @override
  String get authAttestationReady => 'token توثيق الهاردوير جاهز';

  @override
  String get authAttestationUnavailable =>
      'توثيق الهاردوير مش متاح على الجهاز ده';

  @override
  String get authAuthorizedUser => 'مستخدم مصرّح له';

  @override
  String get authAttestationHeading => 'حالة توثيق الجهاز';

  @override
  String get authAttestationExplainer =>
      'التطبيق ده مش بيطلب tokens من Google Play Integrity أو Apple App Attest. نسخ الـ Debug بتبعت علامة تطوير واضحة، ونسخ الـ Release بتعلن إن توثيق الهاردوير مش مدعوم. السيرفر عنده تحقق من tokens الـ Play Integrity الحقيقية، بس التطبيق ده مقدرش يطلّعها من غير SDK من الشركة ومفاتيح Play Console.';

  @override
  String get authSessionOperations => 'إدارة الجلسة';

  @override
  String get hostdCodeCommandHardDenied =>
      'الأمر ده ممنوع في أي مساحة شغل لأنه ممكن يعمل ضرر كبير.';

  @override
  String get hostdCodeDestructivePathOutsideWorkspace =>
      'الأمر ده بيمسح أو بيغيّر ملفات برّه مساحة الشغل، علشان كده اترفض حتى لو مساحة الشغل موثوقة.';

  @override
  String get hostdCodeDestructiveAfterLeavingWorkspace =>
      'الأمر ده بيخرج من فولدر مساحة الشغل وبعدين بيمسح أو بيغيّر ملفات، علشان كده اترفض حتى لو مساحة الشغل موثوقة.';

  @override
  String get hostdCodeUntrustedShellSyntax =>
      'مساحة الشغل اللي مش موثوقة بتشغّل أمر واحد بسيط للقراية بس: من غير ربط أوامر (&&، ;، |) ولا redirection ولا substitution.';

  @override
  String get hostdCodeCommandNeedsApproval =>
      'مساحة الشغل دي مش موثوقة، علشان كده الأمر ده مستني موافقتك.';

  @override
  String get hostdCodeCommandNotAllowlisted =>
      'مساحة الشغل دي مش موثوقة والأمر ده مش أمر قراية بس. خلّي مساحة الشغل موثوقة، أو افتح الـ Desktop IDE علشان توافق عليه.';

  @override
  String get hostdCodeWriteNeedsApproval =>
      'مساحة الشغل دي مش موثوقة، علشان كده تعديل الملف ده مستني موافقتك.';

  @override
  String get hostdCodeProtectedGitHooks =>
      'الكتابة جوّه .git/hooks ممنوعة دايمًا: الـ hook برنامج Git بيشغّله مع أول Commit أو Checkout أو Push.';

  @override
  String get hostdCodeProtectedGitConfig =>
      'الكتابة في .git/config ممنوعة دايمًا: ممكن تخلّي Git يشغّل برنامج تاني.';

  @override
  String get hostdCodeRootNotADirectory =>
      'الفولدر ده مش موجود، أو مش فولدر أصلًا.';

  @override
  String get hostdCodeRootIsFilesystemRoot =>
      'مينفعش درايف كامل يبقى مساحة شغل. اختار فولدر مشروع جوّاه.';

  @override
  String get hostdCodeRootDenylisted =>
      'فولدر السيستم ده مينفعش يبقى مساحة شغل خالص. اختار فولدر مشروع جوّاه.';

  @override
  String get hostdCodeOutsideWorkspace =>
      'المسار ده برّه مساحة الشغل. ضيفه كمساحة شغل لوحده علشان تستخدمه.';

  @override
  String get hostdCodeUnauthorized =>
      'الـ Host Daemon رفض الطلب علشان مفتاح الوصول مش مطابق. شغّل الـ Host Daemon من الأول وجرّب تاني.';

  @override
  String hostdCodeInternalError(String detail) {
    return 'الـ Host Daemon حصلت عنده مشكلة: $detail';
  }

  @override
  String get hostdCodeMissingParameter =>
      'الطلب اللي راح للـ Host Daemon كان ناقصه حاجة. حدّث التطبيق وجرّب تاني.';

  @override
  String get hostdCodeUnknownAction =>
      'الـ Host Daemon مش عارف الإجراء ده. ممكن يكون قديم.';

  @override
  String get hostdCodeRootNotGranted =>
      'الفولدر ده مبقاش من مساحات الشغل بتاعتك. ضيفه تاني وجرّب تاني.';

  @override
  String get hostdCodePathNotFound => 'الملف أو الفولدر ده مش موجود.';

  @override
  String get hostdCodeFileNotUtf8 =>
      'الملف ده مش نص، علشان كده مينفعش يتعرض هنا.';

  @override
  String get hostdCodeApprovalNotFound =>
      'الطلب ده اترد عليه قبل كده أو انتهت مدته.';

  @override
  String get hostdCodeNotAGitRepository => 'الفولدر ده مش ريبو Git.';

  @override
  String get hostdCodeGitOutputNotUtf8 =>
      'Git رجّع حاجة مش نص، علشان كده مينفعش تتعرض هنا.';

  @override
  String hostdCodeGitCommandFailed(String detail) {
    return 'Git فشل: $detail';
  }

  @override
  String get hostdCodeUnknownDiagnosticsTool =>
      'الـ Host Daemon مش عارف أداة الفحص دي.';

  @override
  String get hostdCodeTaskNotFound =>
      'مساحة الشغل بتاعة مهمة المهندس دي مبقتش موجودة.';

  @override
  String hostdCodePairingFailed(String detail) {
    return 'مقدرناش نبدأ الربط مع السيرفر: $detail';
  }

  @override
  String get hostdCodeNoPairingSession =>
      'مفيش ربط شغّال دلوقتي. ابدأ الربط من الأول.';

  @override
  String get hostdCodePairingEnded => 'الربط خلص قبل ما يتوافق عليه.';

  @override
  String get hostdCodeWorkspaceNotTrusted =>
      'مساحة الشغل دي مش موثوقة. خلّيها موثوقة الأول علشان تفتح التيرمينال أو الـ Debugger هنا.';

  @override
  String hostdCodeTerminalStartFailed(String detail) {
    return 'مقدرناش نشغّل التيرمينال: $detail';
  }

  @override
  String hostdCodeDebugStartFailed(String detail) {
    return 'مقدرناش نشغّل الـ Debugger (debugpy): $detail';
  }

  @override
  String hostdCodeDebugUnreachable(String detail) {
    return 'الـ Debugger (debugpy) اشتغل بس مقدرناش نوصله: $detail';
  }

  @override
  String get coreEngineerWorkspacesLoadFailed =>
      'معرفناش نحمّل مساحات الشغل بتاعتك. شغلك مضاعش -- جرّب تاني بعد شوية.';

  @override
  String get voiceSocketUnavailable => 'الصوت المباشر مش متاح على السيرفر ده.';

  @override
  String get voiceSocketTooMany =>
      'الصوت المباشر مفتوح على جهاز تاني، أو شغّلته مرات كتير. اقفله من هناك أو استنى كام دقيقة وجرّب تاني.';

  @override
  String get voiceSocketBadRequest =>
      'السيرفر مقدرش يبدأ الصوت المباشر بالإعدادات دي.';

  @override
  String get voiceClientNothingSpeakable => 'مفيش حاجة في الرد ده تتقري بصوت.';

  @override
  String get voiceClientCouldNotRead => 'معرفناش نقرا الرد ده بصوت.';

  @override
  String get hostStatusRepairRequired => 'محتاج تعمل Pair تاني';

  @override
  String get engineeringDaemonRepairRequired =>
      'محتاج تعمل Pair تاني: السيرفر مبقاش بيقبل الجهاز ده';

  @override
  String knowledgeErrTooLarge(String kind, String limit) {
    String _temp0 = intl.Intl.selectLogic(kind, {
      'pdf': 'الـ PDF ده',
      'other': 'المستند ده',
    });
    return '$_temp0 أكبر من الحد المسموح على السيرفر ($limit).';
  }

  @override
  String get knowledgeErrRateLimited =>
      'رفعت ملفات كتير دلوقتي. استنى دقيقة وجرّب تاني.';

  @override
  String get knowledgeErrUnavailable =>
      'مكتبة المعرفة مش متاحة دلوقتي. جرّب تاني بعدين.';

  @override
  String knowledgeErrInProgress(String kind) {
    String _temp0 = intl.Intl.selectLogic(kind, {
      'pdf': 'الـ PDF ده',
      'other': 'المستند ده',
    });
    return 'AURA لسه بيحفظ $_temp0. اعمل تحديث بعد شوية قبل ما تجرّب تاني.';
  }

  @override
  String knowledgeErrTimeout(String kind) {
    String _temp0 = intl.Intl.selectLogic(kind, {
      'pdf': 'الـ PDF ده',
      'other': 'المستند ده',
    });
    return 'AURA واخد وقت طويل مع $_temp0. ممكن يكون اتحفظ: اعمل تحديث وشوف. لو جرّبت تاني مش هيتضاف مرتين.';
  }

  @override
  String get knowledgeErrNetwork =>
      'معرفناش نوصل لـ AURA. لو جرّبت تاني مش هيتضاف مرتين.';

  @override
  String get knowledgeErrSignIn => 'سجّل دخول تاني عشان تكمّل.';

  @override
  String get knowledgeErrForbidden => 'معندكش صلاحية تضيف مستندات.';

  @override
  String knowledgeErrServer(String kind) {
    String _temp0 = intl.Intl.selectLogic(kind, {
      'pdf': 'الـ PDF ده',
      'other': 'المستند ده',
    });
    return 'AURA معرفش يحفظ $_temp0. لو جرّبت تاني مش هيتضاف مرتين.';
  }

  @override
  String knowledgeErrRejected(String kind, String reason) {
    String _temp0 = intl.Intl.selectLogic(kind, {
      'pdf': 'الـ PDF ده',
      'other': 'المستند ده',
    });
    return 'معرفناش نضيف $_temp0: $reason';
  }

  @override
  String get knowledgeAlreadyUnchanged =>
      'موجود في مكتبتك بالفعل ومتغيرش. مفيش حاجة جديدة اتحفظت.';

  @override
  String get knowledgeNotSavedQuarantined =>
      'ماتحفظش: كل الأجزاء اتحجزت في فحص الأمان.';

  @override
  String knowledgeNotSavedReason(String reason) {
    return 'ماتحفظش ($reason).';
  }

  @override
  String get hostRepairExplanation =>
      'السيرفر مبقاش بيقبل الجهاز ده (انتهت صلاحيته، أو اتلغى، أو حسابك معندوش صلاحية المهندس). اعمل Pair تاني عشان تتوصل.';

  @override
  String knowledgeDeleteFailedReason(String reason) {
    return 'معرفناش نمسح المستند ده: $reason';
  }

  @override
  String get knowledgeDeleteUnreachable =>
      'معرفناش نوصل لـ AURA عشان نمسح المستند ده.';

  @override
  String get homeInsightDismissFailed =>
      'معرفناش نخفي الملاحظة دي. جرّب تاني بعد شوية.';

  @override
  String get engineerConsentTitle => 'المهندس بيستخدم Claude من Anthropic';

  @override
  String get engineerConsentBodyWhat =>
      'علشان يشتغل على الكود بتاعك، المهندس بيبعت اللي بيقراه واللي بيشغّله لـ Claude، وده نموذج ذكاء اصطناعي بتشغّله Anthropic، شركة برّه AURA.';

  @override
  String get engineerConsentBodyIncludes =>
      'ده بيشمل الرسايل اللي بتبعتها للمهندس، ومحتوى الملفات اللي بيفتحها في الفولدرات اللي سمحت بيها أو الريبوهات اللي ربطتها، ونتيجة الأوامر اللي بيشغّلها — واللي ممكن يكون فيها أي حاجة الأوامر دي بتطبعها.';

  @override
  String get engineerConsentBodyChoice =>
      'مفيش حاجة بتتبعت غير لما توافق. تقدر تسحب موافقتك من الإعدادات ← المهندس في أي وقت؛ وساعتها المهندس بيقف لحد ما توافق تاني. باقي مميزات AURA مش بتتأثر.';

  @override
  String get engineerConsentAgree => 'موافق، كمّل';

  @override
  String get engineerConsentNotNow => 'مش دلوقتي';

  @override
  String engineerConsentSaveFailed(String reason) {
    return 'مقدرناش نحفظ ردّك. $reason';
  }

  @override
  String get engineerConsentUpdateTitle => 'حدّث AURA علشان تستخدم المهندس';

  @override
  String get engineerConsentUpdateBody =>
      'التنبيه اللي بيوضّح المهندس بيبعت إيه لـ Anthropic اتغيّر، والنسخة دي من التطبيق مش هتقدر تعرض الجديد. حدّث التطبيق علشان تقراه وتكمّل.';

  @override
  String get engineerConsentUpdateOk => 'تمام';

  @override
  String get engineerConsentNeededNotice =>
      'المهندس مشغّلش ده: محتاج موافقتك قبل ما يبعت أي حاجة لـ Anthropic. مفيش حاجة اتبعتت لـ Anthropic.';

  @override
  String engineerLimitReached(String time) {
    return 'وصلت للحد بتاع المهندس النهارده. هيتجدد الساعة $time.';
  }

  @override
  String get engineerLimitReachedNoTime =>
      'وصلت للحد بتاع المهندس النهارده. جرّب تاني بعد ما يتجدد.';

  @override
  String get engineerConsentStatusTitle => 'مشاركة الكود مع Anthropic';

  @override
  String engineerConsentStatusOn(String date) {
    return 'مشاركة الكود مع Anthropic: شغّالة من $date';
  }

  @override
  String get engineerConsentStatusOnNoDate =>
      'مشاركة الكود مع Anthropic: شغّالة';

  @override
  String get engineerConsentStatusOff => 'مشاركة الكود مع Anthropic: مقفولة';

  @override
  String get engineerConsentStatusOnSubtitle =>
      'المهندس بيبعت اللي بيقراه واللي بيشغّله لـ Claude';

  @override
  String get engineerConsentStatusOffSubtitle =>
      'المهندس بيسأل قبل ما يبعت أي حاجة';

  @override
  String get engineerConsentStatusLoading => 'جاري التحقق…';

  @override
  String get engineerConsentStatusError =>
      'مقدرناش نتحقق. دوس علشان تجرّب تاني.';

  @override
  String get engineerConsentWithdraw => 'اسحب الموافقة';

  @override
  String get engineerConsentWithdrawTitle => 'توقف مشاركة الكود مع Anthropic؟';

  @override
  String get engineerConsentWithdrawBody =>
      'المهندس هيقف لحد ما توافق تاني. باقي مميزات AURA مش هتتأثر.';

  @override
  String get engineerConsentWithdrawn =>
      'اتسحبت الموافقة. المهندس هيسأل قبل ما يبعت أي حاجة تاني.';

  @override
  String engineerConsentWithdrawFailed(String reason) {
    return 'مقدرناش نسحب الموافقة. $reason';
  }

  @override
  String get desktopIdeUnavailableTitle =>
      'الـ Desktop IDE لسه مش متاح على المنصة دي';

  @override
  String get desktopIdeUnavailableBody =>
      'بيشتغل في AURA على ويندوز ولينكس. كل حاجة تانية في AURA شغالة هنا عادي.';

  @override
  String get desktopIdeUnavailableGoHome => 'روح للشاشة الرئيسية';

  @override
  String get hostdRemoteControlTitle => 'اسمح بالتحكم من موبايلي ومن الويب';

  @override
  String get hostdRemoteControlExplanation =>
      'لما تكون شغّالة، المهندس يقدر يشتغل على الفولدرات المسموح بيها على الكمبيوتر ده من موبايلك أو من الويب. كل تغيير وكل أمر لازم توافق عليه من الموبايل أو الويب الأول. تقدر تقفلها من هنا في أي وقت.';

  @override
  String get hostdRemoteControlConfirmTitle =>
      'تسمح بالتحكم من موبايلك ومن الويب؟';

  @override
  String get hostdRemoteControlConfirmBody =>
      'أي حد مسجّل دخول على حساب AURA بتاعك من موبايل أو من متصفح هيقدر يطلب من المهندس يقرا ويغيّر ويشغّل أوامر في الفولدرات المسموح بيها على الكمبيوتر ده. كل تغيير وكل أمر برضه هيستنى الموافقة على الجهاز اللي الطلب جاي منه. تقدر تقفل الخاصية دي من هنا في أي وقت.';

  @override
  String get hostdRemoteControlConfirmAllow => 'اسمح';

  @override
  String hostdRemoteControlFailed(String error) {
    return 'مقدرناش نغيّر التحكم عن بُعد: $error';
  }

  @override
  String get engineerDesktopNotConnected => 'الكمبيوتر بتاعك مش متوصّل';

  @override
  String get engineerDesktopRemoteOff =>
      'التحكم عن بُعد مقفول على الكمبيوتر بتاعك — شغّله من AURA على الكمبيوتر';

  @override
  String get engineerDesktopNoFolders =>
      'الكمبيوتر بتاعك لسه مسمحش بأي فولدر — ضيف واحد من AURA على الكمبيوتر';

  @override
  String get engineerDesktopFolderLabel => 'فولدر على الكمبيوتر بتاعك';

  @override
  String get engineerDesktopCheckFailed => 'مقدرناش نشيّك على الكمبيوتر بتاعك';

  @override
  String get engineerDesktopRefresh => 'شيّك تاني';

  @override
  String get hostdCodeGitPushNeedsApproval =>
      'الـ agent لما يعمل أي git push بيستنى موافقتك الأول.';

  @override
  String get hostdCodePathOutsideTrustedFolder =>
      'الأمر ده بيوصل لحاجات برّه الفولدر الموثوق ده، علشان كده مستني موافقتك.';

  @override
  String get hostdCodeRemoteCommandNeedsConfirmation =>
      'الأوامر اللي جاية من الموبايل أو الويب مستنية موافقتك هناك الأول.';

  @override
  String get hostdCodeProtectedGitDirFile =>
      'الكتابة في ملف .git بيوجّه Git لمكان تاني ممنوعة دايمًا.';

  @override
  String get hostdCodeRemoteControlDisabled =>
      'التحكم عن بُعد مقفول على الكمبيوتر ده. شغّل «اسمح بالتحكم من موبايلي ومن الويب» على الكمبيوتر.';

  @override
  String get hostdCodeRemoteConfirmationRequired =>
      'التغييرات اللي جاية من الموبايل أو الويب لازم توافق عليها هناك الأول.';

  @override
  String get hostdCodeInvalidTaskKey =>
      'الـ Host Daemon معرفش المهمة دي. حدّث التطبيق وجرّب تاني.';

  @override
  String get hostdCodeIsolatedTaskUnresolved =>
      'مقدرناش نجهّز نسخة معزولة من الفولدر للمهمة دي.';

  @override
  String get hostdCodeInvalidOrigin =>
      'الـ Host Daemon معرفش الطلب ده جاي منين، علشان كده رفضه. حدّث التطبيق وجرّب تاني.';

  @override
  String get hostdCodeDiagnosticsUntrusted =>
      'المشاكل بتتفحص في الفولدرات الموثوقة بس. خلّي الفولدر ده موثوق علشان تفحصه.';

  @override
  String get hostdCodeInvalidRequestBody =>
      'الطلب اللي راح للـ Host Daemon مكانش بالشكل اللي مستنيه. حدّث التطبيق وجرّب تاني.';

  @override
  String get gitInitBannerTitle => 'الفولدر ده مش ريبو Git';

  @override
  String get gitInitBannerBody =>
      'المهندس بيشتغل في نسخة لوحده من الفولدر علشان كل تغيير يتراجع ويتلغي لو حبيت، وده محتاج Git. ابدأ Git هنا علشان يقدر يشتغل.';

  @override
  String get gitInitButton => 'ابدأ Git هنا';

  @override
  String get gitInitButtonShort => 'ابدأ Git';

  @override
  String get gitInitConfirmTitle => 'تبدأ Git في الفولدر ده؟';

  @override
  String gitInitConfirmBody(String path) {
    return 'ده هيشغّل git init في $path ويعمل أول Commit بالملفات اللي في الفولدر دلوقتي، على الكمبيوتر ده بس. الملفات اللي في الـ .gitignore مش هتدخل؛ أي حاجة تانية — حتى ملفات زي .env — هتدخل في الـ Commit المحلي ده، فلو محتاج .gitignore ضيفه الأول.';
  }

  @override
  String get gitInitConfirmAction => 'ابدأ';

  @override
  String get gitInitDone => 'Git بقى جاهز في الفولدر ده.';

  @override
  String gitInitFailed(String error) {
    return 'مقدرناش نبدأ Git: $error';
  }

  @override
  String get engineerDesktopNotGit =>
      'الفولدر ده لسه مش ريبو Git — المهندس محتاج ده علشان يشتغل على نسخة لوحده.';

  @override
  String get hostdCodeIsolationNeedsGit =>
      'الفولدر ده مش ريبو Git، علشان كده المهندس مش هيقدر يشتغل على نسخة لوحده منه — استخدم «ابدأ Git هنا» الأول.';

  @override
  String get hostdCodeAlreadyAGitRepo => 'الفولدر ده ريبو Git أصلًا.';

  @override
  String hostdCodeGitInitFailed(String detail) {
    return 'مقدرناش نبدأ Git هنا: $detail';
  }

  @override
  String get hostdCodeApprovalQueueFull =>
      'فيه تغييرات كتير مستنية موافقتك؛ رد على شوية منهم الأول.';

  @override
  String get chatApprovalExpiredNotice => 'الموافقة دي انتهت مدتها. اطلب تاني.';

  @override
  String get ideStatusRepairTooltip =>
      'السيرفر مبقاش بيقبل الكمبيوتر ده. دوس علشان تعمل Pair تاني.';

  @override
  String get explorerRevealWindowsOnly =>
      'الإظهار في نظام الملفات شغال على ويندوز بس دلوقتي';

  @override
  String get loginApple => 'تسجيل الدخول بـ Apple';

  @override
  String get loginEmail => 'تسجيل الدخول بالإيميل';

  @override
  String get emailFieldLabel => 'الإيميل';

  @override
  String get passwordFieldLabel => 'الباسورد';

  @override
  String get confirmPasswordFieldLabel => 'أكّد الباسورد';

  @override
  String get nameFieldLabel => 'الاسم';

  @override
  String get emailSignInAction => 'دخول';

  @override
  String get emailCreateAccountAction => 'اعمل حساب';

  @override
  String get emailForgotPassword => 'نسيت الباسورد؟';

  @override
  String get emailSwitchToRegister => 'جديد على AURA؟ اعمل حساب';

  @override
  String get emailSwitchToSignIn => 'عندك حساب؟ سجّل دخول';

  @override
  String get emailSendReset => 'ابعت لينك التغيير';

  @override
  String get emailResetSent =>
      'لو فيه حساب بالإيميل ده، بعتنالك لينك تغيّر بيه الباسورد.';

  @override
  String get emailInvalid => 'اكتب إيميل صحيح.';

  @override
  String get passwordTooShort => 'استخدم 8 حروف على الأقل.';

  @override
  String get passwordsDontMatch => 'الباسوردين مش زي بعض.';

  @override
  String get verifyEmailBanner =>
      'شوف الإيميل بتاعك وأكّد عنوانك. هتحتاجه عشان تعمل تيم أو تنضم لتيم.';

  @override
  String get verifyEmailResend => 'ابعت تاني';

  @override
  String get identityWrongCredentials =>
      'الإيميل والباسورد دول مش متطابقين مع أي حساب.';

  @override
  String get identityEmailInUse =>
      'فيه حساب بالإيميل ده بالفعل. سجّل دخول بدل كده.';

  @override
  String get identityWeakPassword => 'اختار باسورد أقوى.';

  @override
  String get identityInvalidEmail => 'الإيميل ده مش صحيح.';

  @override
  String get identityNetwork =>
      'مش قادرين نوصل لخدمة تسجيل الدخول. اتأكد من النت.';

  @override
  String get identityDifferentMethod =>
      'الإيميل ده مستخدم بطريقة دخول تانية. سجّل دخول بيها.';

  @override
  String get identityUnavailable => 'طريقة الدخول دي مش متاحة دلوقتي.';

  @override
  String get verifyEmailDone => 'أكّدت خلاص';

  @override
  String get workspaceTileTitle => 'مساحة الشغل';

  @override
  String get workspacePersonal => 'شخصي';

  @override
  String get workspaceSwitcherTitle => 'غيّر مساحة الشغل';

  @override
  String get workspaceCreateTeam => 'اعمل تيم';

  @override
  String get workspaceTeamSettings => 'إعدادات التيم';

  @override
  String get workspaceRoleOwner => 'المالك';

  @override
  String get workspaceRoleAdmin => 'أدمن';

  @override
  String get workspaceRoleMember => 'عضو';

  @override
  String get teamNameLabel => 'اسم التيم';

  @override
  String get teamCreateAction => 'اعمل';

  @override
  String get teamMembersSection => 'الأعضاء';

  @override
  String get teamInvitesSection => 'دعوات مستنية';

  @override
  String get teamYou => 'إنت';

  @override
  String get teamInviteAction => 'ادعي';

  @override
  String get teamInviteEmailLabel => 'الإيميل';

  @override
  String get teamInviteSentTitle => 'الدعوة اتعملت';

  @override
  String teamInviteSentBody(String email) {
    return 'ابعت اللينك ده لـ $email. بيشتغل مرة واحدة وبينتهي بعد كام يوم. لو الإيميل متظبط في AURA، بعتناه كمان.';
  }

  @override
  String get teamCopyLink => 'انسخ اللينك';

  @override
  String get teamLinkCopied => 'اللينك اتنسخ';

  @override
  String get teamMakeAdmin => 'خليه أدمن';

  @override
  String get teamMakeMember => 'خليه عضو';

  @override
  String get teamMakeOwner => 'خليه المالك';

  @override
  String get teamRemove => 'شيله من التيم';

  @override
  String get teamRevokeInvite => 'الغي';

  @override
  String teamMakeOwnerConfirm(String who) {
    return 'تخلّي $who المالك؟ إنت هتبقى أدمن، وهو بس اللي يقدر يدير الدفع أو يمسح التيم.';
  }

  @override
  String teamRemoveConfirm(String who) {
    return 'تشيل $who من التيم؟';
  }

  @override
  String get teamLeave => 'اخرج من التيم';

  @override
  String teamLeaveConfirm(String name) {
    return 'تخرج من $name؟ مش هتقدر توصل لمستنداته ومحادثاته المشتركة.';
  }

  @override
  String get teamDelete => 'امسح التيم';

  @override
  String teamDeleteConfirm(String name) {
    return 'تمسح $name عند الكل؟ مستنداته ومحادثاته المشتركة هتتمسح. مفيش رجوع في الخطوة دي.';
  }

  @override
  String get teamReadOnlyNote =>
      'المالك والأدمن بس يقدروا يدعوا أو يغيّروا الأعضاء.';

  @override
  String get planTitle => 'الباقة والاستخدام';

  @override
  String planCurrent(String plan) {
    return 'الباقة: $plan';
  }

  @override
  String planMessages(String used, String allowed) {
    return 'رسايل الشهر ده: $used من $allowed';
  }

  @override
  String planResets(String date) {
    return 'بيتصفّر يوم $date';
  }

  @override
  String planStorage(String used, String allowed) {
    return 'مساحة المعرفة: $used من $allowed';
  }

  @override
  String get planEngineerIncluded => 'وكيل الـ Engineer موجود';

  @override
  String get planEngineerNotIncluded => 'وكيل الـ Engineer: في باقات Pro وTeam';

  @override
  String planSeats(String seats) {
    return 'المقاعد: $seats';
  }

  @override
  String planSubscriptionStatus(String status) {
    return 'الاشتراك: $status';
  }

  @override
  String planPaidThrough(String date) {
    return 'مدفوع لحد $date';
  }

  @override
  String planGraceUntil(String date) {
    return 'وصول كامل لحد $date، بعدها الباقة المجانية';
  }

  @override
  String get planPaymentsSection => 'المدفوعات';

  @override
  String get planUpgradeSection => 'الترقية';

  @override
  String get planUpgradePro => 'اشترك في Pro';

  @override
  String get planUpgradeTeam => 'اشترك في Team';

  @override
  String get planSeatsLabel => 'عدد المقاعد';

  @override
  String get planNameFree => 'المجانية';

  @override
  String get planNamePro => 'Pro';

  @override
  String get planNameTeam => 'Team';

  @override
  String get planSeatsInvalid => 'اكتب عدد مقاعد صحيح، 1 أو أكتر.';

  @override
  String get subscriptionStatusPending => 'مستني الدفع';

  @override
  String get subscriptionStatusActive => 'شغّال';

  @override
  String get subscriptionStatusPastDue => 'الدفع متأخر';

  @override
  String get subscriptionStatusGrace => 'في فترة السماح';

  @override
  String get subscriptionStatusCanceled => 'اتلغى';

  @override
  String get subscriptionStatusExpired => 'خلص';

  @override
  String get planTeamNotInApp => 'باقات الفرق مش متاحة للشرا من التطبيق ده.';

  @override
  String planStoreBuy(String price) {
    return 'رقّي لـ Pro · $price';
  }

  @override
  String get planStoreWaiting => 'الدفع وصل. بنفعّل باقتك…';

  @override
  String get planStorePending => 'الدفع وصل. باقتك هتتحدّث خلال دقايق.';

  @override
  String get planStoreManage => 'إدارة الاشتراك';

  @override
  String get planStoreOtherDevice =>
      'اشتراكك بيتدار من متجر التطبيقات على جهازك التاني.';

  @override
  String get planStoreElsewhere =>
      'الاشتراك ده بيتدار من App Store أو Google Play.';

  @override
  String get planFawryActive => 'عندك اشتراك بالفعل.';

  @override
  String get planStoreFailed => 'عملية الشرا ما تمّتش.';

  @override
  String get planOwnerOnly => 'مالك مساحة الشغل بس يقدر يغيّر الباقة.';

  @override
  String get planBillingOff => 'الترقية لسه مش متاحة.';

  @override
  String get planCancel => 'الغي الاشتراك';

  @override
  String get planCancelConfirm =>
      'تلغي الاشتراك؟ باقتك هتفضل لحد آخر المدة اللي دفعتها.';

  @override
  String get checkoutTitle => 'ادفع بفوري';

  @override
  String checkoutReference(String reference) {
    return 'الرقم المرجعي: $reference';
  }

  @override
  String checkoutAmount(String amount) {
    return 'المبلغ: $amount';
  }

  @override
  String checkoutExpires(String date) {
    return 'ادفع قبل $date';
  }

  @override
  String get checkoutHowTo =>
      'ادفع من أي منفذ فوري، أو من محفظة الموبايل، أو من تطبيق myFawry بالرقم المرجعي ده. باقتك هتبدأ أول ما الفلوس توصل.';

  @override
  String get seePlans => 'شوف الباقات';

  @override
  String get inviteAcceptTitle => 'انضم لتيم';

  @override
  String get inviteAcceptBody => 'اتعزمت تنضم لتيم على AURA.';

  @override
  String get inviteAcceptAction => 'انضم';

  @override
  String inviteAccepted(String name) {
    return 'انضميت لـ $name';
  }

  @override
  String get inviteInvalid => 'الدعوة دي مبقتش صالحة. اطلب واحدة جديدة.';

  @override
  String get inviteNeedsVerifiedEmail =>
      'أكّد الإيميل بتاعك الأول، وبعدين افتح اللينك تاني.';

  @override
  String get shareWithTeam => 'شارك مع التيم';

  @override
  String sharedWithTeam(String name) {
    return 'اتشاركت مع $name';
  }

  @override
  String get teamChatsTitle => 'محادثات التيم';

  @override
  String get teamChatsEmpty => 'لسه محدش شارك محادثات مع التيم ده.';

  @override
  String teamChatsSharedBy(String who) {
    return 'شاركها $who';
  }

  @override
  String get teamChatsUnshare => 'الغي المشاركة';

  @override
  String get teamChatsTooMany =>
      'شاركت أقصى عدد محادثات مسموح. الغي مشاركة واحدة الأول.';

  @override
  String get signInMethodsTitle => 'طرق الدخول';

  @override
  String get signInMethodsSubtitle => 'Google وApple والإيميل';

  @override
  String get signInMethodLinked => 'متوصّل';

  @override
  String get signInMethodLink => 'وصّل';

  @override
  String get signInMethodInUse => 'طريقة الدخول دي تبع حساب AURA تاني.';

  @override
  String get signInMethodLinkedDone => 'طريقة الدخول اتوصّلت';

  @override
  String get legalAgreePrefix => 'لما تكمّل تبقى موافق على';

  @override
  String get legalTerms => 'شروط الخدمة';

  @override
  String get legalPrivacy => 'سياسة الخصوصية';

  @override
  String get legalRefund => 'سياسة الاسترداد';

  @override
  String get deleteBlockedTitle => 'إنت مالك تيم';

  @override
  String get deleteBlockedBody =>
      'قبل ما تمسح حسابك، خلّي حد تاني مالك التيمات دي، أو امسحها:';

  @override
  String get shareNothing => 'المحادثة دي لسه مفيهاش حاجة تتشارك.';

  @override
  String shareConfirm(String name) {
    return 'تشارك نسخة من المحادثة دي مع $name؟ كل اللي في التيم هيقدروا يقروها.';
  }

  @override
  String get twoStepTitle => 'التحقق بخطوتين';

  @override
  String get twoStepSignInBody => 'اكتب الكود من تطبيق المصادقة.';

  @override
  String get twoStepCodeLabel => 'الكود (6 أرقام)';

  @override
  String get twoStepVerify => 'تأكيد';

  @override
  String get twoStepWrongCode =>
      'الكود مش مطابق. استخدم أحدث كود من تطبيق المصادقة.';

  @override
  String get twoStepUnsupported =>
      'الخطوة التانية لتسجيل الدخول في الحساب ده مش مدعومة في التطبيق. كلّم الدعم يعيدوا ضبطها.';

  @override
  String get identityReauthRequired =>
      'عشان أمانك، اخرج وادخل تاني وبعدين جرّب تاني.';

  @override
  String get identityWrongCode => 'الكود مش مطابق.';

  @override
  String get twoStepIntro =>
      'ضيف كود من تطبيق مصادقة (Google Authenticator أو Microsoft Authenticator أو 1Password…) لكل مرة تسجّل فيها دخول.';

  @override
  String get twoStepVerifyEmailFirst =>
      'أكّد إيميلك الأول، وبعدين ارجع فعّل الخاصية دي.';

  @override
  String get twoStepSetUp => 'إعداد';

  @override
  String get twoStepScanOrEnter =>
      'ضيف المفتاح ده لتطبيق المصادقة، وبعدين اكتب الكود اللي من 6 أرقام اللي بيظهر.';

  @override
  String get twoStepOpenApp => 'افتح في تطبيق المصادقة';

  @override
  String get twoStepCopyKey => 'انسخ المفتاح';

  @override
  String get twoStepOn => 'مفعّل: تسجيل الدخول بيطلب كود من تطبيق المصادقة.';

  @override
  String get twoStepTurnOff => 'إيقاف';

  @override
  String get twoStepTurnOffConfirm =>
      'تسجيل الدخول مش هيطلب كود تاني. توقف التحقق بخطوتين؟';

  @override
  String get twoStepEnabled => 'التحقق بخطوتين اتفعّل.';

  @override
  String get twoStepNoApp =>
      'مفيش تطبيق مصادقة فتحه. انسخ المفتاح وحطّه في تطبيق المصادقة بنفسك.';

  @override
  String get twoStepAuthenticatorApp => 'تطبيق المصادقة';

  @override
  String get loginBrowserHandoff =>
      'تكمّل في المتصفح — Google أو Apple أو الإيميل';

  @override
  String coreDesktopSignInBrowserError(String error) {
    return 'تسجيل الدخول في المتصفح رجّع مشكلة: $error';
  }

  @override
  String get coreDesktopSignInNoSession => 'سيرفر AURA مرجّعش جلسة دخول.';

  @override
  String get desktopSignInTitle => 'سجّل دخول على تطبيق AURA للكمبيوتر';

  @override
  String get desktopSignInSubtitle =>
      'اختار هتسجّل دخول إزاي. لما تخلّص، التاب ده هيرجّع تسجيل الدخول لتطبيق الكمبيوتر.';

  @override
  String get desktopSignInSharedComputer =>
      'AURA هيخرّجك من المتصفح ده تاني أول ما تطبيق الكمبيوتر ياخد تسجيل الدخول.';

  @override
  String get desktopSignInWorking => 'بنكمّل تسجيل الدخول…';

  @override
  String get desktopSignInDone =>
      'سجّلت الدخول. بنرجّعك لتطبيق AURA على الكمبيوتر…';

  @override
  String get desktopSignInDoneHint =>
      'لو محصلش حاجة، ارجع لتطبيق الكمبيوتر. تقدر تقفل التاب ده.';

  @override
  String get desktopSignInInvalidLink =>
      'لينك تسجيل الدخول ده مش صالح. ابدأ تاني من تطبيق AURA على الكمبيوتر: اختار \"تكمّل في المتصفح\" في شاشة تسجيل الدخول.';

  @override
  String get desktopSignInWebOnly =>
      'الصفحة دي للمتصفح. في تطبيق الكمبيوتر، اختار \"تكمّل في المتصفح\" في شاشة تسجيل الدخول وهو هيفتحلك الصفحة دي.';

  @override
  String get desktopSignInGoToLogin => 'روح لتسجيل الدخول';

  @override
  String get desktopSignInServerUnreachable =>
      'مقدرناش نوصل لسيرفر AURA. اتأكد من الاتصال وجرّب تاني.';

  @override
  String desktopSignInServerFailed(int status) {
    return 'سيرفر AURA مقدرش يوصّل تسجيل الدخول ده لتطبيق الكمبيوتر (HTTP $status).';
  }

  @override
  String get desktopSignInNoIdentityToken =>
      'خدمة تسجيل الدخول مرجّعتش identity token للحساب ده. جرّب تاني.';

  @override
  String get desktopSignInReturnFailed =>
      'مقدرناش نرجع لتطبيق الكمبيوتر. اتأكد إنه لسه مستنيك، وبعدين ابدأ تاني منه.';

  @override
  String get chatToolAsksApproval => 'بيطلب موافقتك قبل ما يتنفّذ';

  @override
  String get chatToolChangesThings => 'الإجراء ده بيغيّر حاجات برّه الشات.';

  @override
  String get engineeringTasksTooltip => 'مهام الوكيل';

  @override
  String get engineeringRunSearchTooltip => 'دوّر في التشغيلات';

  @override
  String get engineeringUsageTooltip => 'الاستهلاك والتكلفة';

  @override
  String get agentTasksTitle => 'مهام الوكيل';

  @override
  String get agentTasksGoalHint => 'اوصف المهمة للوكيل';

  @override
  String get agentTasksSend => 'ابعت المهمة';

  @override
  String get agentTasksWaiting => 'مستنية تتبعت';

  @override
  String get agentTasksSending => 'بتتبعت…';

  @override
  String get agentTasksNotSent => 'ماتبعتتش';

  @override
  String agentTasksRetryingIn(int count) {
    return 'المحاولة رقم $count فشلت. هتتحاول تاني لوحدها.';
  }

  @override
  String get agentTasksQueuedOffline => 'اتحفظت. هتتبعت أول ما النت يرجع.';

  @override
  String get agentTasksSentSnack => 'المهمة اتبعتت.';

  @override
  String get agentTasksAlreadySentSnack =>
      'المهمة دي كانت وصلت قبل كده. ماتنفذتش مرتين.';

  @override
  String get agentTasksNotSentSnack =>
      'السيرفر رفض المهمة دي. شوف السبب في القايمة.';

  @override
  String get agentTasksQueueFull =>
      'فيه مهام كتير لسه ماتبعتتش. استنى تتبعت أو شيل منها.';

  @override
  String get agentTasksRetryNow => 'حاول تاني دلوقتي';

  @override
  String get agentTasksDismiss => 'شيل';

  @override
  String get agentTasksSendAgain => 'ابعتها تاني كمهمة جديدة';

  @override
  String get agentTasksSentTitle => 'آخر المهام';

  @override
  String get agentTasksNoneYet => 'مفيش مهام لسه';

  @override
  String get agentTasksNoneYetMessage =>
      'المهام اللي بتبعتها هتظهر هنا مع حالتها.';

  @override
  String get agentTasksConflict =>
      'الطلب ده اتستخدم قبل كده بمحتوى مختلف، فماتبعتش.';

  @override
  String get agentTaskStatusQueued => 'في الطابور';

  @override
  String get agentTaskStatusRunning => 'شغّالة';

  @override
  String get agentTaskStatusAwaitingApproval => 'مستنية موافقتك';

  @override
  String get agentTaskStatusSucceeded => 'خلصت';

  @override
  String get agentTaskStatusFailed => 'فشلت';

  @override
  String get agentTaskStatusCancelled => 'اتلغت';

  @override
  String get runSearchTitle => 'سجل التشغيلات';

  @override
  String get runSearchHint => 'دوّر بالوكيل أو الحالة أو الأداة';

  @override
  String get runSearchButton => 'دوّر';

  @override
  String runSearchResults(int count) {
    return '$count تشغيلة';
  }

  @override
  String get runSearchEmpty => 'مفيش تشغيلات مطابقة';

  @override
  String get runSearchEmptyMessage =>
      'جرّب كلمة تانية، أو امسح البحث عشان تشوف أحدث التشغيلات.';

  @override
  String get runStatusFailed => 'فشلت';

  @override
  String get runStatusCompleted => 'خلصت';

  @override
  String get runStatusPartial => 'خلصت جزئياً';

  @override
  String get runStatusRunning => 'شغّالة';

  @override
  String get runStatusAwaitingApproval => 'مستنية موافقة';

  @override
  String get runStatusBudgetExhausted => 'وقفت عند الحد';

  @override
  String runSummaryLine(int steps, int tools) {
    return '$steps خطوات في الخطة، $tools استدعاء أداة';
  }

  @override
  String get runTimelineTitle => 'خط سير التشغيلة';

  @override
  String get runTimelineNoSteps => 'مفيش خطوات اتسجلت للتشغيلة دي.';

  @override
  String runTimelineIteration(int n) {
    return 'الدورة $n';
  }

  @override
  String get runTimelineStepFailed => 'فشلت';

  @override
  String get runMetricElapsed => 'المدة';

  @override
  String get runMetricTokens => 'التوكنز';

  @override
  String get runMetricToolCalls => 'استدعاءات الأدوات';

  @override
  String get runMetricIterations => 'الدورات';

  @override
  String runSeconds(String seconds) {
    return '$seconds ث';
  }

  @override
  String get usageTitle => 'الاستهلاك والتكلفة';

  @override
  String get usagePeriodDay => '24 ساعة';

  @override
  String get usagePeriodWeek => '7 أيام';

  @override
  String get usagePeriodMonth => '30 يوم';

  @override
  String get usageGroupDay => 'باليوم';

  @override
  String get usageGroupAgent => 'بالوكيل';

  @override
  String get usageGroupTask => 'بالمهمة';

  @override
  String get usageTotalTurns => 'الردود';

  @override
  String get usageTotalTokensIn => 'توكنز داخلة';

  @override
  String get usageTotalTokensOut => 'توكنز خارجة';

  @override
  String get usageTotalCost => 'التكلفة';

  @override
  String usageFailedTurns(int count) {
    return '$count فشلت';
  }

  @override
  String usageCostValue(String amount) {
    return '$amount دولار';
  }

  @override
  String get usageTruncated =>
      'الأرقام ممكن تكون ناقصة: السيرفر بيحتفظ بآخر الردود بس.';

  @override
  String get usageDisabled => 'الاستهلاك مش متسجل على السيرفر ده.';

  @override
  String get usageNoData => 'مفيش حاجة متسجلة في الفترة دي';

  @override
  String get usageNoTask => 'من غير مهمة';

  @override
  String get usageBreakdown => 'التفصيل';

  @override
  String get usageRecentTurns => 'آخر الردود';

  @override
  String usageTurnLine(int tokens, String seconds) {
    return '$tokens توكن، $seconds ث';
  }

  @override
  String get usageTurnFailed => 'فشل';

  @override
  String get usageTurnCached => 'من الكاش';

  @override
  String get hostdCodeProtectedGitInternals =>
      'الكتابة جوّه .git (HEAD أو الـ index أو الـ refs أو الـ objects) ممنوعة دايمًا: ممكن تبوّظ الـ repository. استخدم أوامر Git بدالها.';
}
