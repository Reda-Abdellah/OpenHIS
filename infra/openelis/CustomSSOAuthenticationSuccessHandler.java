/*
 * Decompiled with CFR 0.152.
 * 
 * Could not load the following classes:
 *  jakarta.servlet.ServletException
 *  jakarta.servlet.http.HttpServletRequest
 *  jakarta.servlet.http.HttpServletResponse
 *  jakarta.servlet.http.HttpSession
 *  org.json.JSONObject
 *  org.openelisglobal.common.action.IActionConstants
 *  org.openelisglobal.common.log.LogEvent
 *  org.openelisglobal.common.util.ConfigurationProperties
 *  org.openelisglobal.common.util.validator.GenericValidator
 *  org.openelisglobal.common.validator.BaseErrors
 *  org.openelisglobal.common.valueholder.BaseObject
 *  org.openelisglobal.login.valueholder.LoginUser
 *  org.openelisglobal.login.valueholder.UserSessionData
 *  org.openelisglobal.role.service.RoleService
 *  org.openelisglobal.role.valueholder.Role
 *  org.openelisglobal.systemuser.service.SystemUserService
 *  org.openelisglobal.systemuser.valueholder.SystemUser
 *  org.openelisglobal.systemusermodule.service.PermissionModuleService
 *  org.openelisglobal.systemusermodule.valueholder.PermissionModule
 *  org.openelisglobal.userrole.service.UserRoleService
 *  org.springframework.beans.factory.annotation.Autowired
 *  org.springframework.beans.factory.annotation.Value
 *  org.springframework.security.core.Authentication
 *  org.springframework.security.core.GrantedAuthority
 *  org.springframework.security.core.context.SecurityContextHolder
 *  org.springframework.security.oauth2.core.user.DefaultOAuth2User
 *  org.springframework.security.saml2.provider.service.authentication.DefaultSaml2AuthenticatedPrincipal
 *  org.springframework.security.web.authentication.SavedRequestAwareAuthenticationSuccessHandler
 *  org.springframework.stereotype.Component
 *  org.springframework.validation.Errors
 *  org.springframework.web.servlet.support.RequestContextUtils
 */
package org.openelisglobal.security.login;

import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.HttpSession;
import java.io.IOException;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import org.json.JSONObject;
import org.openelisglobal.common.action.IActionConstants;
import org.openelisglobal.common.log.LogEvent;
import org.openelisglobal.common.util.ConfigurationProperties;
import org.openelisglobal.common.util.validator.GenericValidator;
import org.openelisglobal.common.validator.BaseErrors;
import org.openelisglobal.common.valueholder.BaseObject;
import org.openelisglobal.login.valueholder.LoginUser;
import org.openelisglobal.login.valueholder.UserSessionData;
import org.openelisglobal.role.service.RoleService;
import org.openelisglobal.role.valueholder.Role;
import org.openelisglobal.systemuser.service.SystemUserService;
import org.openelisglobal.systemuser.valueholder.SystemUser;
import org.openelisglobal.systemusermodule.service.PermissionModuleService;
import org.openelisglobal.systemusermodule.valueholder.PermissionModule;
import org.openelisglobal.userrole.service.UserRoleService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.core.user.DefaultOAuth2User;
import org.springframework.security.saml2.provider.service.authentication.DefaultSaml2AuthenticatedPrincipal;
import org.springframework.security.web.authentication.SavedRequestAwareAuthenticationSuccessHandler;
import org.springframework.stereotype.Component;
import org.springframework.validation.Errors;
import org.springframework.web.servlet.support.RequestContextUtils;

@Component
public class CustomSSOAuthenticationSuccessHandler
extends SavedRequestAwareAuthenticationSuccessHandler
implements IActionConstants {
    @Autowired
    private UserRoleService userRoleService;
    @Autowired
    private PermissionModuleService<PermissionModule> permissionModuleService;
    @Autowired
    private SystemUserService systemUserService;
    @Autowired
    private RoleService roleService;
    @Value(value="${org.openelisglobal.timezone:}")
    private String timezone;
    public static final int DEFAULT_SESSION_TIMEOUT_IN_MINUTES = 20;

    public void onAuthenticationSuccess(HttpServletRequest request, HttpServletResponse response, Authentication authentication) throws IOException, ServletException {
        String xfHeader = request.getHeader("X-Forwarded-For");
        if (xfHeader == null) {
            LogEvent.logInfo((String)((Object)((Object)this)).getClass().getSimpleName(), (String)"onSuccess", (String)("Successful login attempt for " + authentication.getName() + " from " + request.getRemoteAddr()));
        } else {
            LogEvent.logInfo((String)((Object)((Object)this)).getClass().getSimpleName(), (String)"onSuccess", (String)("Successful login attempt for " + authentication.getName() + " from " + xfHeader.split(",")[0]));
        }
        boolean apiLogin = "true".equals(request.getParameter("apiCall"));
        boolean samlLogin = false;
        boolean oauthLogin = false;
        SecurityContextHolder.getContext().setAuthentication(authentication);
        if (authentication != null) {
            Object principal = authentication.getPrincipal();
            if (principal instanceof DefaultSaml2AuthenticatedPrincipal) {
                DefaultSaml2AuthenticatedPrincipal samlUser = (DefaultSaml2AuthenticatedPrincipal)principal;
                request.getSession().setAttribute("samlSession", (Object)true);
                samlLogin = true;
                try {
                    this.setupUserSession(request, samlUser);
                }
                catch (IllegalStateException e) {
                    LogEvent.logError((String)((Object)((Object)this)).getClass().getSimpleName(), (String)"onAuthenticationSuccess", (String)"the login user doesn't exist in OE this is usually caused by login being handled by an external application that contains a user that OE is missing");
                    SecurityContextHolder.getContext().setAuthentication(null);
                    BaseErrors errors = new BaseErrors();
                    errors.reject("login.error.noOeUser");
                    request.getSession().setAttribute("loginErrors", (Object)errors);
                    this.getRedirectStrategy().sendRedirect(request, response, "/LoginPage");
                    return;
                }
                catch (RuntimeException e) {
                    LogEvent.logError((Throwable)e);
                    SecurityContextHolder.getContext().setAuthentication(null);
                    BaseErrors errors = new BaseErrors();
                    errors.reject("login.error.sessionsetup");
                    request.getSession().setAttribute("loginErrors", (Object)errors);
                    this.getRedirectStrategy().sendRedirect(request, response, "/LoginPage");
                    return;
                }
            }
            if (principal instanceof DefaultOAuth2User) {
                DefaultOAuth2User oauthUser = (DefaultOAuth2User)principal;
                request.getSession().setAttribute("oauthSession", (Object)true);
                oauthLogin = true;
                try {
                    this.setupUserSession(request, oauthUser);
                }
                catch (IllegalStateException e) {
                    LogEvent.logError((String)((Object)((Object)this)).getClass().getSimpleName(), (String)"onAuthenticationSuccess", (String)"the login user doesn't exist in OE this is usually caused by login being handled by an external application that contains a user that OE is missing");
                    SecurityContextHolder.getContext().setAuthentication(null);
                    BaseErrors errors = new BaseErrors();
                    errors.reject("login.error.noOeUser");
                    request.getSession().setAttribute("loginErrors", (Object)errors);
                    this.getRedirectStrategy().sendRedirect(request, response, "/LoginPage");
                    return;
                }
                catch (RuntimeException e) {
                    LogEvent.logError((Throwable)e);
                    SecurityContextHolder.getContext().setAuthentication(null);
                    BaseErrors errors = new BaseErrors();
                    errors.reject("login.error.sessionsetup");
                    request.getSession().setAttribute("loginErrors", (Object)errors);
                    this.getRedirectStrategy().sendRedirect(request, response, "/LoginPage");
                    return;
                }
            }
        }
        if (apiLogin) {
            request.getSession().setAttribute("login_method", (Object)"form");
            this.handleApiLogin(request, response);
        } else if (samlLogin) {
            request.getSession().setAttribute("login_method", (Object)"samlLogin");
            this.getRedirectStrategy().sendRedirect(request, response, "/Home");
        } else if (oauthLogin) {
            request.getSession().setAttribute("login_method", (Object)"oauthLogin");
            this.handleApiLogin(request, response);
        } else {
            super.onAuthenticationSuccess(request, response, authentication);
            this.clearCustomAuthenticationAttributes(request);
        }
    }

    private void handleApiLogin(HttpServletRequest request, HttpServletResponse response) throws IOException {
        PrintWriter out = response.getWriter();
        response.setContentType("application/json");
        out.print(new JSONObject().put("success", true));
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    private void setupUserSession(HttpServletRequest request, DefaultSaml2AuthenticatedPrincipal principal) {
        Collection authorities = SecurityContextHolder.getContext().getAuthentication().getAuthorities();
        boolean isAdmin = false;
        for (Object _a : authorities) { GrantedAuthority authority = (GrantedAuthority) _a;
            String[] authorityExplode = authority.getAuthority().split("-");
            if (authorityExplode.length < 2) continue;
            isAdmin = "admin".equalsIgnoreCase(authorityExplode[1]);
        }
        request.getSession().setMaxInactiveInterval(1200);
        UserSessionData usd = new UserSessionData();
        Optional user = this.systemUserService.getMatch("loginName", (Object)principal.getName());
        SystemUser systemUser = new SystemUser();
        if (user.isEmpty()) {
            systemUser.setFirstName(principal.getName());
            systemUser.setLastName("");
            systemUser.setLoginName(principal.getName());
            systemUser.setIsActive("Y");
            systemUser.setIsEmployee("Y");
            systemUser.setExternalId("1");
            String initial = (GenericValidator.isBlankOrNull((String)systemUser.getFirstName()) ? "" : systemUser.getFirstName().substring(0, 1)) + (GenericValidator.isBlankOrNull((String)systemUser.getLastName()) ? "" : systemUser.getLastName().substring(0, 1));
            systemUser.setInitials(initial);
            systemUser.setSysUserId("1");
            systemUser = (SystemUser)this.systemUserService.save(systemUser);
        } else {
            systemUser = (SystemUser)user.get();
        }
        usd.setSytemUserId(Integer.parseInt(systemUser.getId()));
        usd.setLoginName(principal.getName());
        usd.setElisUserName(principal.getName());
        usd.setUserTimeOut(1200);
        usd.setAdmin(isAdmin);
        request.getSession().setAttribute("userSessionData", (Object)usd);
        request.getSession().setAttribute("timezone", (Object)this.timezone);
        if (ConfigurationProperties.getInstance().getPropertyValue("permissions.agent").equalsIgnoreCase("ROLE")) {
            Set<String> permittedPages = this.getPermittedForms(authorities);
            request.getSession().setAttribute("permittedActions", permittedPages);
        }
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    private void setupUserSession(HttpServletRequest request, DefaultOAuth2User principal) {
        Collection authorities = SecurityContextHolder.getContext().getAuthentication().getAuthorities();
        boolean isAdmin = false;
        for (Object _a : authorities) { GrantedAuthority authority = (GrantedAuthority) _a;
            String[] authorityExplode = authority.getAuthority().split("-");
            if (authorityExplode.length < 2) continue;
            isAdmin = "admin".equalsIgnoreCase(authorityExplode[1]);
        }
        request.getSession().setMaxInactiveInterval(1200);
        UserSessionData usd = new UserSessionData();
        Optional user = this.systemUserService.getMatch("loginName", (Object)principal.getName());
        SystemUser systemUser = new SystemUser();
        if (user.isEmpty()) {
            systemUser.setFirstName(principal.getName());
            systemUser.setLastName("");
            systemUser.setLoginName(principal.getName());
            systemUser.setIsActive("Y");
            systemUser.setIsEmployee("Y");
            systemUser.setExternalId("1");
            String fn = systemUser.getFirstName();
            String ln = systemUser.getLastName();
            String initial = (fn != null && !fn.isEmpty() ? fn.substring(0, 1) : "")
                           + (ln != null && !ln.isEmpty() ? ln.substring(0, 1) : "");
            systemUser.setInitials(initial);
            systemUser.setSysUserId("1");
            systemUser = (SystemUser)this.systemUserService.save(systemUser);
        } else {
            systemUser = (SystemUser)user.get();
        }
        usd.setSytemUserId(Integer.parseInt(systemUser.getId()));
        usd.setLoginName(principal.getName());
        usd.setElisUserName(principal.getName());
        usd.setUserTimeOut(1200);
        usd.setAdmin(isAdmin);
        request.getSession().setAttribute("authorities", (Object)usd);
        request.getSession().setAttribute("userSessionData", (Object)usd);
        request.getSession().setAttribute("timezone", (Object)this.timezone);
        if (ConfigurationProperties.getInstance().getPropertyValue("permissions.agent").equalsIgnoreCase("ROLE")) {
            Set<String> permittedPages = this.getPermittedForms(authorities);
            request.getSession().setAttribute("permittedActions", permittedPages);
        }
    }

    private Set<String> getPermittedForms(Collection<? extends GrantedAuthority> authorities) {
        HashSet<String> allPermittedPages = new HashSet<String>();
        ArrayList<String> roleIds = new ArrayList<String>();
        for (GrantedAuthority grantedAuthority : authorities) {
            String role;
            String[] authorityExplode = grantedAuthority.getAuthority().split("-");
            if (authorityExplode.length < 2 || GenericValidator.isBlankOrNull((String)(role = this.getRoleForAuthority(authorityExplode[1])))) continue;
            roleIds.add(role);
        }
        for (String string : roleIds) {
            Set permittedPagesForRole = this.permissionModuleService.getAllPermittedPagesFromAgentId(Integer.parseInt(string));
            allPermittedPages.addAll(permittedPagesForRole);
        }
        return allPermittedPages;
    }

    private String getRoleForAuthority(String string) {
        Optional sysRole = this.roleService.getMatch("name", (Object)string);
        if (sysRole.isPresent()) {
            return ((Role)sysRole.get()).getId();
        }
        LogEvent.logWarn((String)((Object)((Object)this)).getClass().getSimpleName(), (String)"getRoleForAuthority", (String)("could not find a role for the authority: " + string));
        return null;
    }

    private Set<String> getPermittedForms(int systemUserId) {
        HashSet<String> allPermittedPages = new HashSet<String>();
        List<?> roleIds = this.userRoleService.getRoleIdsForUser(Integer.toString(systemUserId));
        for (Object _r : roleIds) { String roleId = (String) _r;
            Set permittedPagesForRole = this.permissionModuleService.getAllPermittedPagesFromAgentId(Integer.parseInt(roleId));
            allPermittedPages.addAll(permittedPagesForRole);
        }
        return allPermittedPages;
    }

    private boolean passwordExpiringSoon(LoginUser loginInfo) {
        return loginInfo.getPasswordExpiredDayNo() <= Integer.parseInt(ConfigurationProperties.getInstance().getPropertyValue("login.user.expired.reminder.day")) && loginInfo.getPasswordExpiredDayNo() > Integer.parseInt(ConfigurationProperties.getInstance().getPropertyValue("login.user.change.allow.day"));
    }

    protected void clearCustomAuthenticationAttributes(HttpServletRequest request) {
        HttpSession session = request.getSession(false);
        if (session == null) {
            return;
        }
        session.removeAttribute("login_errors");
        session.removeAttribute("SPRING_SECURITY_LAST_EXCEPTION");
    }

    protected void addFlashMsgsToRequest(HttpServletRequest request) {
        Map inputFlashMap = RequestContextUtils.getInputFlashMap((HttpServletRequest)request);
        if (inputFlashMap != null) {
            Boolean success = (Boolean)inputFlashMap.get("success");
            request.setAttribute("success", (Object)success);
            String successMessage = (String)inputFlashMap.get("successMessage");
            request.setAttribute("successMessage", (Object)successMessage);
            Errors errors = (Errors)inputFlashMap.get("requestErrors");
            request.setAttribute("successMessage", (Object)errors);
            List messages = (List)inputFlashMap.get("requestMessages");
            request.setAttribute("requestMessages", (Object)messages);
            List warnings = (List)inputFlashMap.get("requstWarnings");
            request.setAttribute("successMessage", (Object)warnings);
        }
    }
}
