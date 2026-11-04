import {
    Component,
    computed,
    onMounted,
    onWillStart,
    onWillUnmount,
    proxy,
    signal,
    t,
    usePlugin,
    useProps,
} from "@odoo/owl";
import { AutoComplete } from "@web/core/autocomplete/autocomplete";
import { browser } from "@web/core/browser/browser";
import { CheckBox } from "@web/core/checkbox/checkbox";
import { DebugModePlugin } from "@web/core/debug_mode_plugin";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { x2ManyCommands } from "@web/core/orm_plugin";
import { BadgeTag } from "@web/core/tags_list/badge_tag";
import { TagsList } from "@web/core/tags_list/tags_list";
import { user } from "@web/core/user";
import { unique } from "@web/core/utils/arrays";
import { useAutofocus, useService } from "@web/core/utils/hooks";
import { fuzzyLookup } from "@web/core/utils/search";
import { useSortable } from "@web/core/utils/sortable_owl";
import { useDebounced } from "@web/core/utils/timing";

/**
 * Technical id prefix of unsaved fixed value columns; ids returned by the
 * server for saved fixed columns use the same prefix. A fixed value column
 * repeats a user-defined text on every exported row and is not linked to any
 * field of the model.
 */
const FIXED_VALUE_PREFIX = "__fixed__:";

class DeleteExportListDialog extends Component {
    static components = { Dialog };
    static template = "web.DeleteExportListDialog";
    props = useProps({
        text: t.string(),
        close: t.function(),
        delete: t.function(),
    });
    async onDelete() {
        await this.props.delete();
        this.props.close();
    }
}

class ExportDataItem extends Component {
    static template = "web.ExportDataItem";
    static components = { ExportDataItem };
    props = useProps({
        // array of export templates — legacy `{ type: Object }` was never enforced
        exportList: t.array().optional(),
        field: t.object().optional(),
        filterSubfields: t.function(),
        isDebug: t.boolean(),
        isExpanded: t.boolean(),
        isFieldExpandable: t.function(),
        onAdd: t.function(),
        loadFields: t.function(),
    });

    setup() {
        this.state = proxy({
            subfields: [],
        });
        onWillStart(() => {
            if (this.props.isExpanded) {
                // automatically expand the item when subfields are already loaded
                // and display subfields that match the search string
                return this.toggleItem(this.props.field.id, false);
            }
        });
    }

    async toggleItem(id, isUserToggle) {
        if (this.props.isFieldExpandable(id)) {
            if (this.state.subfields.length) {
                this.state.subfields = [];
            } else {
                const subfields = await this.props.loadFields(id, !isUserToggle);
                if (subfields) {
                    this.state.subfields = isUserToggle
                        ? subfields
                        : this.props.filterSubfields(subfields);
                } else {
                    this.state.subfields = [];
                }
            }
        }
    }

    onDoubleClick(id) {
        if (!this.props.isFieldExpandable(id) && !this.isFieldSelected(id)) {
            this.props.onAdd(id);
        }
    }

    isFieldSelected(current) {
        return this.props.exportList.find(({ id }) => id === current);
    }
}

export class ExportDataDialog extends Component {
    static template = "web.ExportDataDialog";
    static components = {
        AutoComplete,
        BadgeTag,
        CheckBox,
        Dialog,
        ExportDataItem,
        TagsList,
    };
    props = useProps({
        close: t.function(),
        context: t.object().optional(),
        defaultExportList: t.array(),
        download: t.function(),
        getExportedFields: t.function(),
        root: t.object(),
    });

    draggableRef = signal.ref();
    searchRef = signal.ref();
    templateNameRef = signal.ref();

    applyChangesTitle = computed(() =>
        this.state.templateName ? this.applyChangesText : this.noChangesText
    );

    debugMode = usePlugin(DebugModePlugin);

    setup() {
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.orm = useService("orm");
        this.uiService = useService("ui");
        useAutofocus({ ref: this.templateNameRef });

        this.knownFields = {};
        this.expandedFields = {};
        this.availableFormats = [];
        this.templates = [];
        this.languagesInstalled = [];
        this.isCompatible = false;

        this.state = proxy({
            exportList: [],
            exportLanguages: [],
            isEditingTemplate: false,
            search: [],
            selectedFormat: 0,
            templateId: null,
            templateName: "",
            isSmall: this.uiService.isSmall,
            disabled: false,
            fixedTitle: "",
            fixedValue: "",
            editingFixedId: null,
        });

        this.removeFieldText = _t("Remove field");
        this.applyChangesText = _t("Apply changes");
        this.noChangesText = _t("No changes to apply");
        this.fixedColumnText = _t("Fixed");
        this.addFixedColumnText = _t("Add fixed value column");
        this.editFixedColumnText = _t("Edit fixed value column");
        this.fixedTitlePlaceholder = _t("Column title");
        this.fixedValuePlaceholder = _t("Fixed value");
        this.fixedTitleRequiredText = _t(
            "The fixed value column must have a title."
        );
        this.fixedColumnsSkippedText = _t(
            "Fixed value columns do not correspond to any field of the model and "
            + "cannot be reimported, so they are not supported by import-compatible "
            + "exports."
        );

        this.fixedColumnSeq = 0;

        this.debouncedOnResize = useDebounced(this.updateSize.bind(this), 300);

        useSortable({
            // Params
            ref: this.draggableRef,
            elements: ".o_export_field",
            ignore:
                ".o_remove_field, .o_edit_fixed_field, .o_fixed_apply, .o_fixed_cancel, input, button",
            enable: !this.state.isSmall,
            cursor: "grabbing",
            // Hooks
            onDrop: async ({ element, previous, next }) => {
                const indexes = [element, previous, next].map(
                    (e) =>
                        e &&
                        Object.values(this.state.exportList).findIndex(
                            ({ id }) => id === e.dataset.field_id
                        )
                );
                let target;
                if (indexes[0] < indexes[1]) {
                    target = previous ? indexes[1] : 0;
                } else {
                    target = next ? indexes[2] : this.state.exportList.length - 1;
                }
                this.onDraggingEnd(indexes[0], target);
            },
        });

        onWillStart(async () => {
            // formats and installed languages only change when a module or a
            // language is installed, unlike the templates this dialog itself
            // creates, edits and deletes
            [this.availableFormats, this.templates, this.languagesInstalled] = await Promise.all([
                rpc("/web/export/formats", {}, { cache: true }),
                this.orm.searchRead(
                    "ir.exports",
                    [["resource", "=", this.props.root.resModel]],
                    [],
                    {
                        context: this.props.context,
                    }
                ),
                this.orm.cache().searchRead("res.lang", [], ["code", "name"], { order: "name" }),
            ]);
            await this.fetchFields();
        });

        onMounted(() => {
            browser.addEventListener("resize", this.debouncedOnResize);
            this.updateSize();
        });

        onWillUnmount(() => browser.removeEventListener("resize", this.debouncedOnResize));
    }

    get fieldsAvailable() {
        if (this.searchRef() && this.searchRef().value) {
            return this.state.search.length && Object.values(this.state.search);
        }
        return Object.values(this.knownFields);
    }

    /**
     * Languages are only worth selecting when at least one field of the export
     * list is translatable: without any, every language would produce the very
     * same columns, so the selector is hidden instead of being a no-op.
     */
    get canExportTranslations() {
        return (
            this.languagesInstalled.length > 1 &&
            this.state.exportList.some(({ translate }) => translate)
        );
    }

    get languageChoices() {
        return this.languagesInstalled
            .filter(({ code }) => !this.state.exportLanguages.some((lang) => lang.code === code))
            .map((language) => ({ value: language, label: language.name }));
    }

    get userLanguageName() {
        const userLanguage = user.lang.replace("-", "_");
        return (
            this.languagesInstalled.find(({ code }) => code === userLanguage)?.name || userLanguage
        );
    }

    get languageSources() {
        return [{ options: this.loadLanguageOptions.bind(this) }];
    }

    loadLanguageOptions(request) {
        const choices = request
            ? fuzzyLookup(request, this.languageChoices, (choice) => choice.label)
            : this.languageChoices;
        return choices.map(({ value, label }) => ({
            label,
            onSelect: () => this.onAddLanguage(value),
        }));
    }

    get languageTags() {
        return this.state.exportLanguages.map(({ code, name }) => ({
            id: code,
            text: name,
            onDelete: () => {
                this.state.exportLanguages = this.state.exportLanguages.filter(
                    (lang) => lang.code !== code
                );
                this.resetTemplateSelection();
            },
        }));
    }

    get rootFields() {
        if (this.searchRef() && this.searchRef().value) {
            const rootFromSearchResults = this.fieldsAvailable.map((f) => {
                if (f.parent) {
                    const parentEl = this.knownFields[f.parent.id];
                    return this.knownFields[parentEl.parent ? parentEl.parent.id : parentEl.id];
                }
                return this.knownFields[f.id];
            });
            return unique(rootFromSearchResults);
        }
        return this.fieldsAvailable.filter(({ parent }) => !parent);
    }

    filterSubfields(subfields) {
        let subfieldsFromSearchResults = [];
        let searchResults;
        if (this.searchRef() && this.searchRef().value) {
            searchResults = this.lookup(this.searchRef().value);
        }
        const fieldsAvailable = Object.values(searchResults || this.knownFields);
        if (this.searchRef() && this.searchRef().value) {
            subfieldsFromSearchResults = fieldsAvailable
                .filter((f) => f.parent && this.knownFields[f.parent.id].parent)
                .map((f) => f.parent);
        }
        const availableSubFields = unique([...fieldsAvailable, ...subfieldsFromSearchResults]);
        return subfields.filter((a) => availableSubFields.some((b) => a.id === b.id));
    }

    updateSize() {
        this.state.isSmall = this.uiService.isSmall;
    }

    /**
     * Load fields to display and (re)set the list of available fields.
     *
     * @param {boolean} [keepExportList=false] when true, the current export
     *  list is left untouched. This is used when only the available fields
     *  change (e.g. toggling the "Updatable fields only" switch), which must
     *  not affect the fields the user already chose to export.
     */
    async fetchFields(keepExportList = false) {
        this.knownFields = {};
        this.expandedFields = {};
        await this.loadFields();
        if (!keepExportList) {
            await this.setDefaultExportList();
        }
        this.state.search = [];
        if (this.searchRef()) {
            this.searchRef().value = "";
        }
        if (this.state.templateId && !keepExportList) {
            this.loadExportList(this.state.templateId);
        }
    }

    /**
     * Changing the export list or the selected languages while a template is
     * loaded but not being edited detaches from that template: the current
     * selection is kept as a plain (unsaved) working set, allowing the user
     * to build a new template on top of an existing one without altering it.
     */
    resetTemplateSelection() {
        if (this.state.templateId && !this.state.isEditingTemplate) {
            this.state.templateId = null;
        }
    }

    isFieldExpandable(id) {
        return this.knownFields[id].children && id.split("/").length < 3;
    }

    async loadExportList(value) {
        this.state.templateId = value ? Number(value) : null;
        if (!value) {
            return;
        }
        const { fields, export_languages } = await rpc("/web/export/namelist", {
            model: this.props.root.resModel,
            export_id: Number(value),
        });
        // Don't safe the result in this.knownFields because, the result is only partial
        const fixedColumns = fields.filter((field) => field.is_fixed);
        if (this.isCompatible && fixedColumns.length) {
            // Fixed value columns cannot be reimported: the import-compatible
            // export rejects them server-side as well, skip them here too.
            this.notification.add(this.fixedColumnsSkippedText, { type: "warning" });
        }
        this.state.exportList =
            this.isCompatible && fixedColumns.length
                ? fields.filter((field) => !field.is_fixed)
                : fields;
        this.state.editingFixedId = null;
        this.state.exportLanguages = this.languagesInstalled.filter(({ code }) =>
            export_languages.includes(code)
        );
    }

    async loadFields(id, preventLoad = false) {
        let parentField, parentParams;
        if (id) {
            if (this.expandedFields[id]) {
                // we don't make a new RPC if the value is already known
                return this.expandedFields[id].fields;
            }
            parentField = this.knownFields[id];
            parentParams = {
                ...parentField.params,
                parent_field_type: parentField.field_type,
                parent_field: parentField,
                parent_name: parentField.string,
                exclude: [parentField.relation_field],
            };
        }
        if (preventLoad) {
            return;
        }
        const fields = await this.props.getExportedFields(this.isCompatible, parentParams);
        for (const field of fields) {
            field.parent = parentField;
            if (!this.knownFields[field.id]) {
                this.knownFields[field.id] = field;
            }
        }
        if (id) {
            this.expandedFields[id] = { fields };
        }
        return fields;
    }

    onDraggingEnd(item, target) {
        this.state.exportList.splice(target, 0, this.state.exportList.splice(item, 1)[0]);
        this.resetTemplateSelection();
    }

    onAddItemExportList(fieldId) {
        this.state.exportList.push(this.knownFields[fieldId]);
        this.resetTemplateSelection();
    }

    onRemoveItemExportList(fieldId) {
        const item = this.state.exportList.findIndex(({ id }) => id === fieldId);
        this.state.exportList.splice(item, 1);
        if (this.state.editingFixedId === fieldId) {
            this.state.editingFixedId = null;
        }
        this.resetTemplateSelection();
    }

    onFixedComposerInput(attr, ev) {
        this.state[attr] = ev.target.value;
    }

    onFixedComposerKeydown(ev) {
        if (ev.key === "Enter" && this.state.fixedTitle.trim()) {
            ev.preventDefault();
            this.onAddFixedColumn();
        }
    }

    onAddFixedColumn() {
        const title = this.state.fixedTitle.trim();
        if (!title) {
            this.notification.add(this.fixedTitleRequiredText, { type: "danger" });
            return;
        }
        this.state.exportList.push({
            id: `${FIXED_VALUE_PREFIX}new-${this.fixedColumnSeq++}`,
            string: title,
            field_type: "fixed",
            is_fixed: true,
            fixed_value: this.state.fixedValue,
        });
        this.state.fixedTitle = "";
        this.state.fixedValue = "";
        this.resetTemplateSelection();
    }

    onEditFixedColumn(field) {
        this._fixedSnapshot = {
            string: field.string,
            fixed_value: field.fixed_value,
        };
        this.state.editingFixedId = field.id;
    }

    onFixedFieldInput(field, attr, ev) {
        field[attr] = ev.target.value;
        this.resetTemplateSelection();
    }

    onSaveFixedColumn(field) {
        const title = field.string.trim();
        if (!title) {
            this.notification.add(this.fixedTitleRequiredText, { type: "danger" });
            return;
        }
        field.string = title;
        this.state.editingFixedId = null;
        this.resetTemplateSelection();
    }

    onCancelFixedColumnEdit(field) {
        Object.assign(field, this._fixedSnapshot || {});
        this.state.editingFixedId = null;
    }

    onFixedFieldKeydown(field, ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.onSaveFixedColumn(field);
        } else if (ev.key === "Escape") {
            ev.preventDefault();
            this.onCancelFixedColumnEdit(field);
        }
    }

    async onChangeExportList(ev) {
        this.loadExportList(ev.target.value);
    }

    onCreateExportTemplate() {
        this.state.templateId = "new_template";
        this.state.templateName = "";
        this.state.isEditingTemplate = true;
    }

    onEditExportTemplate() {
        const template = this.templates.find(({ id }) => id === this.state.templateId);
        this.state.templateName = template ? template.name : "";
        this.state.isEditingTemplate = true;
    }

    onTemplateNameKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            if (this.state.templateName) {
                this.onSaveExportTemplate();
            }
        } else if (ev.key === "Escape") {
            ev.preventDefault();
            this.onCancelExportTemplate();
        }
    }

    async onSaveExportTemplate() {
        const name = this.state.templateName;
        if (!name) {
            return;
        }
        const exportLanguageIds = this.canExportTranslations
            ? this.state.exportLanguages.map(({ id }) => id)
            : [];
        const exportFieldsCommands = this.state.exportList.map((field, index) =>
            field.is_fixed
                ? x2ManyCommands.create(false, {
                      line_type: "fixed",
                      name: field.string,
                      fixed_value: field.fixed_value || "",
                      sequence: (index + 1) * 10,
                  })
                : x2ManyCommands.create(false, {
                      name: field.id,
                      sequence: (index + 1) * 10,
                  })
        );
        if (this.state.templateId === "new_template") {
            const [id] = await this.orm.create(
                "ir.exports",
                [
                    {
                        name,
                        export_fields: exportFieldsCommands,
                        export_language_ids: [x2ManyCommands.set(exportLanguageIds)],
                        resource: this.props.root.resModel,
                    },
                ],
                { context: this.props.context }
            );
            this.templates.push({ id, name });
            this.state.templateId = id;
        } else {
            const id = this.state.templateId;
            await this.orm.write(
                "ir.exports",
                [id],
                {
                    name,
                    export_fields: [x2ManyCommands.clear(), ...exportFieldsCommands],
                    export_language_ids: [x2ManyCommands.set(exportLanguageIds)],
                },
                { context: this.props.context }
            );
            const template = this.templates.find((i) => i.id === id);
            if (template) {
                template.name = name;
            }
        }
        this.state.isEditingTemplate = false;
    }

    onCancelExportTemplate() {
        this.state.isEditingTemplate = false;
        this.state.editingFixedId = null;
        if (this.state.templateId === "new_template") {
            this.state.templateId = null;
            return;
        }
        this.loadExportList(this.state.templateId);
    }

    async onClickExportButton() {
        if (!this.state.exportList.length) {
            return this.notification.add(_t("Please select fields to save export list..."), {
                type: "danger",
            });
        }
        if (
            this.isCompatible &&
            this.state.exportList.some((field) => field.is_fixed)
        ) {
            // Defense in depth: the server rejects such exports as well.
            return this.notification.add(this.fixedColumnsSkippedText, {
                type: "danger",
            });
        }
        this.state.disabled = true;
        await this.props.download(
            this.getFieldsToExport(),
            this.isCompatible,
            this.availableFormats[this.state.selectedFormat].tag
        );
        this.state.disabled = false;
    }

    async onDeleteExportTemplate() {
        this.dialog.add(DeleteExportListDialog, {
            text: _t("Do you really want to delete this export template?"),
            delete: async () => {
                const id = Number(this.state.templateId);
                await this.orm.unlink("ir.exports", [id], { context: this.props.context });
                this.templates.splice(
                    this.templates.findIndex((i) => i.id === id),
                    1
                );
                this.state.templateId = null;
                this.setDefaultExportList();
            },
        });
    }

    /**
     * Return the fields to export: each translatable field of the export list
     * is replaced by one column per selected export language, using the same
     * `@lang` suffix convention as the import (e.g. `name@fr_FR`) on both the
     * technical name and the label, so the columns stay import-compatible.
     * The unsuffixed column (in the active language) is dropped to avoid
     * exporting the same column twice under different headers.
     */
    getFieldsToExport() {
        if (!this.state.exportLanguages.length) {
            return this.state.exportList;
        }
        return this.state.exportList.flatMap((field) => {
            if (!field.translate) {
                return [field];
            }
            return this.state.exportLanguages.map(({ code }) => ({
                ...field,
                id: `${field.id}@${code}`,
                string: `${field.string}@${code}`,
            }));
        });
    }

    onAddLanguage(language) {
        this.state.exportLanguages.push(language);
        this.resetTemplateSelection();
    }

    onSearch(ev) {
        this.state.search = this.lookup(ev.target.value);
    }

    lookup(value) {
        let lookupResult = fuzzyLookup(
            value,
            Object.values(this.knownFields),
            // because fuzzyLookup gives an higher score if the string starts with the pattern,
            // reversing the string makes the search more reliable in this context
            (field) => field.string.split("/").reverse().join("/")
        );
        if (this.debugMode.isActive()) {
            lookupResult = unique([
                ...lookupResult,
                ...Object.values(this.knownFields).filter((f) => f.id.includes(value)),
            ]);
        }
        return lookupResult;
    }

    async onToggleCompatibleExport(value) {
        this.isCompatible = value;
        await this.fetchFields(true);
        if (value) {
            const fixedColumns = this.state.exportList.filter((field) => field.is_fixed);
            if (fixedColumns.length) {
                this.state.exportList = this.state.exportList.filter(
                    (field) => !field.is_fixed
                );
                this.state.editingFixedId = null;
                this.notification.add(this.fixedColumnsSkippedText, { type: "warning" });
            }
        }
    }

    async setDefaultExportList() {
        const defaultExportList = this.props.defaultExportList
            .map((defaultField) => this.knownFields[defaultField.name])
            .filter((field) => field);

        const defaultExportfields = Object.values(this.knownFields).filter(
            (field) => field.default_export
        );

        this.state.exportList = unique([...defaultExportList, ...defaultExportfields]);
    }

    setFormat(ev) {
        this.state.selectedFormat = this.availableFormats.findIndex(
            ({ tag }) => tag === ev.target.value
        );
    }
}
