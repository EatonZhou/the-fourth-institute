"""One-time ISA name migration for plan_65d54412dc08.

Only persisted-configuration migrations consume this table. Instruction lookup
and execution deliberately do not accept the historical names as aliases.
"""

from types import MappingProxyType


INSTRUCTION_RENAMES = MappingProxyType({
    "PinMailboxItem": "SetMailboxItemPinned",
    "PinPost": "SetPostPinned",
    "FeaturePost": "SetPostFeatured",
    "MuteMailboxItem": "SetMailboxItemMuted",
    "SetMailboxItemHold": "SetMailboxItemHeld",
    "PauseComponent": "SetComponentPaused",
    "OpenIntake": "SetIntakeOpen",
    "MarkMailboxItemRead": "SetMailboxItemRead",
    "MarkChatroomRead": "UpdateChatroomReadPosition",
    "MarkPostRead": "UpdatePostReadVersion",
    "ReorderMailbox": "MoveMailboxItems",
    "OrderDuties": "MoveDuties",
    "RelocateWorkspaceHandoff": "MoveWorkspaceHandoff",
    "PreviewPromptInjection": "GetPromptInjectionPreview",
    "PreviewArticleRestore": "CheckArticleRestore",
    "ProbeModelApi": "CheckModelApi",
    "IssueTemporaryCredential": "CreateTemporaryCredential",
    "RenewTemporaryCredential": "UpdateTemporaryCredential",
    "OpenProviderAccountLogin": "CreateProviderAccountLogin",
    "OpenTerminal": "CreateTerminal",
    "PrepareProviderRuntime": "InstallProviderRuntime",
    "PrepareWorkspaceHandoff": "RunWorkspaceHandoff",
    "StartGoogleDriveBackup": "RunGoogleDriveBackup",
    "CheckFamilyProgress": "MaintainFamilyProgress",
    "CheckFamilyRecovery": "MaintainFamilyRecovery",
    "CheckFamilyReview": "MaintainFamilyReview",
    "CheckFamilyRoleAvailability": "MaintainFamilyRoleAvailability",
    "CheckResidentWorkSummary": "MaintainResidentWorkSummary",
})
