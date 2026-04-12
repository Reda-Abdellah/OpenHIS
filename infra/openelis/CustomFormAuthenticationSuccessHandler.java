/*
 * Patched by OpenHIS — adds OAuth2/OIDC principal support.
 *
 * The stock OpenELIS handler only handles UserDetails (form login). When the
 * SecurityConfig wires this handler for the OAuth2 filter chain, the
 * DefaultOAuth2User principal is silently ignored and setupUserSession(null)
 * throws IllegalStateException.
 *
 * Fix: detect DefaultOAuth2User principal and delegate to the Spring-managed
 * CustomSSOAuthenticationSuccessHandler which already has correct OAuth2
 * auto-provisioning logic.
 */
package org.openelisglobal.security.login;

import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.HttpSession;
import java.io.IOException;
import java.io.PrintWriter;
import java.io.Serializable;
import java.time.OffsetDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.json.JSONObject;
import org.openelisglobal.common.action.IActionConstants;
import org.openelisglobal.common.log.LogEvent;
import org.openelisglobal.common.util.ConfigurationProperties;
import org.openelisglobal.common.validator.BaseErrors;
import org.openelisglobal.login.service.LoginUserService;
import org.openelisglobal.login.valueholder.LoginUser;
import org.openelisglobal.login.valueholder.UserSessionData;
import org.openelisglobal.notifications.dao.NotificationDAO;
import org.openelisglobal.notifications.entity.Notification;
import org.openelisglobal.spring.util.SpringContext;
import org.openelisglobal.systemuser.service.SystemUserService;
import org.openelisglobal.systemuser.valueholder.SystemUser;
import org.openelisglobal.systemusermodule.service.PermissionModuleService;
import org.openelisglobal.systemusermodule.valueholder.PermissionModule;
import org.openelisglobal.userrole.service.UserRoleService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.oauth2.core.user.DefaultOAuth2User;
import org.springframework.security.web.authentication.SavedRequestAwareAuthenticationSuccessHandler;
import org.springframework.stereotype.Component;
import org.springframework.validation.Errors;
import org.springframework.web.servlet.support.RequestContextUtils;

@Component
public class CustomFormAuthenticationSuccessHandler
        extends SavedRequestAwareAuthenticationSuccessHandler
        implements IActionConstants {

    @Autowired
    private LoginUserService loginService;
    @Autowired
    private UserRoleService userRoleService;
    @Autowired
    private PermissionModuleService<PermissionModule> permissionModuleService;
    @Autowired
    private SystemUserService systemUserService;
    @Autowired
    private NotificationDAO notificationDAO;
    @Value("${org.openelisglobal.timezone:}")
    private String timezone;

    public static final int DEFAULT_SESSION_TIMEOUT_IN_MINUTES = 20;

    // ── Resolve services from Spring context (handles new-created instances) ──
    private LoginUserService getLoginService() {
        if (this.loginService != null) return this.loginService;
        return SpringContext.getBean(LoginUserService.class);
    }

    private SystemUserService getSystemUserService() {
        if (this.systemUserService != null) return this.systemUserService;
        return SpringContext.getBean(SystemUserService.class);
    }

    private UserRoleService getUserRoleService() {
        if (this.userRoleService != null) return this.userRoleService;
        return SpringContext.getBean(UserRoleService.class);
    }

    @SuppressWarnings("unchecked")
    private PermissionModuleService<PermissionModule> getPermissionModuleService() {
        if (this.permissionModuleService != null) return this.permissionModuleService;
        return SpringContext.getBean(PermissionModuleService.class);
    }

    private NotificationDAO getNotificationDAO() {
        if (this.notificationDAO != null) return this.notificationDAO;
        return SpringContext.getBean(NotificationDAO.class);
    }

    private String getTimezone() {
        if (this.timezone != null) return this.timezone;
        return "";
    }

    @Override
    public void onAuthenticationSuccess(HttpServletRequest request,
            HttpServletResponse response, Authentication authentication)
            throws IOException, ServletException {

        // ── Log ──
        String xfHeader = request.getHeader("X-Forwarded-For");
        String addr = (xfHeader != null) ? xfHeader.split(",")[0] : request.getRemoteAddr();
        LogEvent.logInfo(getClass().getSimpleName(), "onSuccess",
                "Successful login attempt for " + authentication.getName() + " from " + addr);

        // ── PATCH: if the principal is an OAuth2 user, delegate to the SSO handler
        //    which already handles auto-provisioning and session setup properly ──
        Object principal = (authentication != null) ? authentication.getPrincipal() : null;
        if (principal instanceof DefaultOAuth2User) {
            try {
                CustomSSOAuthenticationSuccessHandler ssoHandler =
                        SpringContext.getBean(CustomSSOAuthenticationSuccessHandler.class);
                ssoHandler.onAuthenticationSuccess(request, response, authentication);
                return;
            } catch (Exception e) {
                LogEvent.logError(getClass().getSimpleName(), "onAuthenticationSuccess",
                        "Failed to delegate OAuth2 to SSO handler: " + e.getMessage());
                LogEvent.logError(e);
                SecurityContextHolder.getContext().setAuthentication(null);
                BaseErrors errors = new BaseErrors();
                errors.reject("login.error.sessionsetup");
                request.getSession().setAttribute("loginErrors", errors);
                getRedirectStrategy().sendRedirect(request, response, "/LoginPage");
                return;
            }
        }

        // ── Original form-login flow (unchanged) ──
        LoginUser loginInfo = null;
        boolean apiLogin = "true".equals(request.getParameter("apiCall"));

        if (principal instanceof UserDetails) {
            UserDetails user = (UserDetails) principal;
            loginInfo = getLoginService().getUserProfile(user.getUsername());
        }

        try {
            setupUserSession(request, loginInfo);
        } catch (IllegalStateException e) {
            LogEvent.logError(getClass().getSimpleName(), "onAuthenticationSuccess",
                    "the login user doesn't exist in OE this is usually caused by "
                    + "login being handled by an external application that contains "
                    + "a user that OE is missing");
            SecurityContextHolder.getContext().setAuthentication(null);
            BaseErrors errors = new BaseErrors();
            errors.reject("login.error.noOeUser");
            request.getSession().setAttribute("loginErrors", errors);
            getRedirectStrategy().sendRedirect(request, response, "/LoginPage");
            return;
        } catch (RuntimeException e) {
            LogEvent.logError(e);
            SecurityContextHolder.getContext().setAuthentication(null);
            BaseErrors errors = new BaseErrors();
            errors.reject("login.error.sessionsetup");
            request.getSession().setAttribute("loginErrors", errors);
            getRedirectStrategy().sendRedirect(request, response, "/LoginPage");
            return;
        }

        if (apiLogin) {
            handleApiLogin(request, response);
        } else {
            super.onAuthenticationSuccess(request, response, authentication);
            clearCustomAuthenticationAttributes(request);
        }
    }

    private void handleApiLogin(HttpServletRequest request, HttpServletResponse response)
            throws IOException {
        PrintWriter out = response.getWriter();
        response.setContentType("application/json");
        out.print(new JSONObject().put("success", true));
    }

    private void setupUserSession(HttpServletRequest request, LoginUser loginInfo) {
        if (loginInfo == null) {
            throw new IllegalStateException("no loginUser during user session setup");
        }
        int timeout = (loginInfo.getUserTimeOut() != null)
                ? Integer.parseInt(loginInfo.getUserTimeOut()) * 60
                : DEFAULT_SESSION_TIMEOUT_IN_MINUTES * 60;
        request.getSession().setMaxInactiveInterval(timeout);
        SystemUser su = (SystemUser) getSystemUserService()
                .get(String.valueOf(loginInfo.getSystemUserId()));
        UserSessionData usd = new UserSessionData();
        usd.setSytemUserId(loginInfo.getSystemUserId());
        usd.setLoginName(loginInfo.getLoginName());
        usd.setElisUserName(su.getNameForDisplay());
        usd.setUserTimeOut(timeout * 60);
        usd.setAdmin(getLoginService().isUserAdmin(loginInfo));
        request.getSession().setAttribute("userSessionData", usd);
        request.getSession().setAttribute("timezone", getTimezone());
        if (ConfigurationProperties.getInstance()
                .getPropertyValue("permissions.agent").equalsIgnoreCase("ROLE")) {
            Set<String> permittedPages = getPermittedForms(usd.getSystemUserId());
            request.getSession().setAttribute("permittedActions", permittedPages);
        }
        if (passwordExpiringSoon(loginInfo)) {
            Notification notification = new Notification();
            notification.setMessage("Your password will expire in "
                    + loginInfo.getPasswordExpiredDayNo() + " day(s). Please update it soon.");
            notification.setUser(su);
            notification.setCreatedDate(OffsetDateTime.now());
            notification.setReadAt(null);
            getNotificationDAO().save(notification);
        }
    }

    private Set<String> getPermittedForms(int systemUserId) {
        HashSet<String> allPermittedPages = new HashSet<>();
        List<String> roleIds = getUserRoleService().getRoleIdsForUser(
                Integer.toString(systemUserId));
        for (String roleId : roleIds) {
            Set<String> permittedPagesForRole = getPermissionModuleService()
                    .getAllPermittedPagesFromAgentId(Integer.parseInt(roleId));
            allPermittedPages.addAll(permittedPagesForRole);
        }
        return allPermittedPages;
    }

    private boolean passwordExpiringSoon(LoginUser loginInfo) {
        return loginInfo.getPasswordExpiredDayNo() <= Integer.parseInt(
                    ConfigurationProperties.getInstance()
                        .getPropertyValue("login.user.expired.reminder.day"))
                && loginInfo.getPasswordExpiredDayNo() > Integer.parseInt(
                    ConfigurationProperties.getInstance()
                        .getPropertyValue("login.user.change.allow.day"));
    }

    protected void clearCustomAuthenticationAttributes(HttpServletRequest request) {
        HttpSession session = request.getSession(false);
        if (session == null) return;
        session.removeAttribute("login_errors");
        session.removeAttribute("SPRING_SECURITY_LAST_EXCEPTION");
    }

    protected void addFlashMsgsToRequest(HttpServletRequest request) {
        Map<String, ?> inputFlashMap = RequestContextUtils.getInputFlashMap(request);
        if (inputFlashMap != null) {
            request.setAttribute("success", inputFlashMap.get("success"));
            request.setAttribute("successMessage", inputFlashMap.get("successMessage"));
            request.setAttribute("requestErrors", inputFlashMap.get("requestErrors"));
            request.setAttribute("requestMessages", inputFlashMap.get("requestMessages"));
            request.setAttribute("requstWarnings", inputFlashMap.get("requstWarnings"));
        }
    }
}
